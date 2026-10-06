import datetime as dt
import itertools
import json
import pathlib
import sys
import uuid
from dataclasses import dataclass

import cache
import db
import game_variant
import tools

# Timeout until a job is considered stuck.
# Lower number might generate duplicate computations,
# a higher number might be required for large
# game variants.
JOB_FAILED_TIMEOUT_SECONDS = 60 * 3

# How many jobs should be waiting in the job
# queue at maximum. Recommend two to three times
# the numer of workers.
JOB_ORDER_MAX_QUEUELENGTH = 20

# How many jobs to generate before setting
# a checkpoint and waiting for all these jobs
# to finish. The lower the number the less
# double-processing in case of an unexpected
# restart, but the more breaks where manager
# is waiting for finished jobs
CHECKPOINTING_JOB_COUNT = 24

# Assignments will be generated whose pebble count is
# at most the sum of min autopilot number, the number
# of buckets, and this additive factor.
ASSIGNMENT_GENERATION_AUTOPILOT_ADDITION = 10


@dataclass
class JobStatus:
    """
    Store information about a job.
    """

    order: cache.JobOrder
    status: cache.JobProcessStatus
    last_contact: dt.datetime
    second_queue: bool = False


OPEN_JOBS: dict[str, JobStatus] = {}


def process_job_status_queue(  # noqa: C901
    valkey: cache.Valkey,
    *,
    fast: bool = False,
    fast_max_messages: int = 100,
    reschedule: bool = False,
    quiet: bool = False,
) -> None:
    """
    Fetch job status data from the queue and update the local
    status about the jobs

    * `fast_max_messages`: If running fast, then this number
      of messages is processed at max before leaving the method.
    * `fast`: If True, then the method just polls the current
      job status and stops. Otherwise, all jobs that are currently
      known to the application will actually be awaited, until
      everything is done.
    * `reschedule`: If either reschedule is True or fast is False,
      jobs that seem to be stuck will be rescheduled.
    * `quiet`: Silences print messages
    """
    # ruff: disable[DTZ005]

    msg_cnt = 0
    empty_cnt = 0

    while True:
        data_raw = cache.pull_job_status(valkey, fast=fast)

        if data_raw is not None:
            msg_cnt += 1
            empty_cnt = 0

            job = cache.JobProcessStatusPush(**json.loads(data_raw[1]))

            if job.id not in OPEN_JOBS:
                if not quiet:
                    print("Received information about a job that I don't know! Ignoring.")
                continue

            if job.status == cache.JobProcessStatus.PREPARED_COMPUTING_JOB:
                if job.data is None:
                    raise RuntimeError(
                        "Received information about a prepared computing job, but no data!",
                    )
                new_job = cache.JobOrder(
                    id=str(uuid.uuid4()),
                    variant_id=job.data["variant-id"],
                    job_type=cache.JobType.ASSIGNMENT_COMPUTER,
                    data={"assignment-id": job.data["assignment-id"]},
                )
                order_job(new_job, valkey, keep_small=False)
                if not quiet:
                    print(
                        "Generating computing job for assignment "
                        f"{job.data['variant-id']}/{job.data['assignment-id']}",
                    )
                # No further processing on such a job
                continue

            job_information = OPEN_JOBS[job.id]
            job_information.status = job.status
            job_information.last_contact = dt.datetime.now()
            if job.status == cache.JobProcessStatus.FINISHED:
                if not quiet:
                    print(f"Job {job.id} is done, popping it")
                del OPEN_JOBS[job.id]

        else:
            empty_cnt += 1

        # Stopping criteria
        if not OPEN_JOBS:
            # Nothing expected to wait for.
            return
        if (not fast and empty_cnt >= 3) or (reschedule and empty_cnt > 0):  # noqa: PLR2004
            # Waiting for finished jobs and there was no update
            # for some time -> Check for stale jobs
            empty_cnt = 0
            for job_id, job_information in OPEN_JOBS.items():
                if dt.datetime.now() - job_information.last_contact > dt.timedelta(
                    seconds=JOB_FAILED_TIMEOUT_SECONDS,
                ):
                    if not quiet:
                        print(f"Job {job_id} seems to be stuck, rescheduling it")
                    # Note: This does a recursive method call,
                    # but `order_job` calls the method with `fast=True`,
                    # so there will be at most one recursive call.
                    order_job(
                        job_information.order,
                        valkey,
                        second_queue=job_information.second_queue,
                    )
                    job_information.last_contact = dt.datetime.now()
        if fast and (data_raw is None or msg_cnt > fast_max_messages):
            # Method should be fast.
            # Either there was nothing to poll, or
            # "enough" messages were polled.
            return


def order_job(
    job: cache.JobOrder,
    valkey: cache.Valkey,
    *,
    second_queue: bool = False,
    keep_small: bool = True,
) -> None:
    """
    Wrapper to push jobs to workers. Prevents the job queue
    to become too long.
    """

    while keep_small and cache.job_order_queuelength(valkey) > JOB_ORDER_MAX_QUEUELENGTH:
        process_job_status_queue(valkey, fast=True, fast_max_messages=10)
        print("Job queue too long, waiting")

    OPEN_JOBS[job.id] = JobStatus(
        order=job,
        status=cache.JobProcessStatus.CREATED,
        last_contact=dt.datetime.now(),  # noqa: DTZ005
        second_queue=second_queue,
    )
    cache.push_job_to_workers(job, valkey)


def compute_equivalent_variants(
    variant_id: int,
    variant: game_variant.GameVariant,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Compute variant equivalences.
    """

    # Ensure that no old jobs are dangling around
    if OPEN_JOBS:
        raise RuntimeError(
            "Some old jobs were dangling around.",
        )

    print("Computing equivalent variants")
    if db.metadata_get_equivalent_variants(variant_id, crs):
        # Computation already done, no need to do it again.
        print("Equivalent variants already computed, skipping.")
        return

    num_fixed_flips = variant.num_buckets // 2

    for combination in itertools.permutations(range(variant.num_buckets), r=num_fixed_flips):
        job_id = str(uuid.uuid4())
        job = cache.JobOrder(
            id=job_id,
            variant_id=variant_id,
            job_type=cache.JobType.EQUIVALENT_VARIANTS_COMPUTATION,
            data={"fixed": combination},
        )
        order_job(job, valkey)

    print("All jobs pushed. Collecting equivalences.")
    known_equivalences = set()

    while OPEN_JOBS:
        process_job_status_queue(valkey, fast=True, reschedule=True)

        while (information_raw := cache.fetch_sent_information(valkey)) is not None:
            information = json.loads(information_raw[1])
            known_equivalences.add(tuple(information["equivalent_variant"]))

    print("Found all equivalent variants, adding to DB now.")
    db.metadata_set_equivalent_variants(variant_id, sorted(known_equivalences), crs)
    print("Finished computing equivalences!")


def compute_bucket_distances(
    variant_id: int,
    variant: game_variant.GameVariant,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Compute variant equivalences.
    """

    # Ensure that no old jobs are dangling around
    if OPEN_JOBS:
        raise RuntimeError(
            "Some old jobs were dangling around.",
        )

    print("Computing bucket distances")
    if db.metadata_get_bucket_distances(variant_id, crs):
        # Computation already done, no need to do it again.
        print("Bucket distances already computed, skipping.")
        return

    known_distances: dict[int, dict[int, int]] = {}

    for bucket0 in range(variant.num_buckets - 1):
        known_distances[bucket0] = {}
        for bucket1 in range(bucket0 + 1, variant.num_buckets):
            job_id = str(uuid.uuid4())
            job = cache.JobOrder(
                id=job_id,
                variant_id=variant_id,
                job_type=cache.JobType.DISTANCES_COMPUTER,
                data={"fixed": [bucket0, bucket1]},
            )
            order_job(job, valkey)

    print("All jobs pushed. Collecting distances.")

    while OPEN_JOBS:
        process_job_status_queue(valkey, fast=True, reschedule=True)

        while (information_raw := cache.fetch_sent_information(valkey)) is not None:
            information = json.loads(information_raw[1])
            known_distances[information["bucket0"]][information["bucket1"]] = information[
                "distance"
            ]

    print("Found all distances, adding to DB now.")
    db.metadata_set_bucket_distances(variant_id, known_distances, crs)
    print("Finished computing equivalences!")


def compute_min_autopilot_number(
    variant_id: int,
    variant: game_variant.GameVariant,
    crs: db.cursor,
) -> None:
    """
    Compute the minimum Autopilot number.
    """

    print("Computing the minimum Autopilot number")
    min_autopilot = variant.num_buckets
    for bob_move in variant.bob_moves:
        min_autopilot += min(len(bob_move.move), variant.num_buckets - len(bob_move.move))

    db.metadata_set_min_autopilot_number(variant_id, min_autopilot, crs)
    print("Minimum autopilot number computation finished.")


def generate_autopilot_variants(
    variant_id: int,
    variant: game_variant.GameVariant,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Generate the Autopilot assignments for a given game variant
    """

    if db.metadata_get_autopilot_generations_finished(variant_id, crs):
        print("Autopilot generation was already done, skipping.")
        return

    print("Working on Autopilot assignment jobs.")

    num_fixed_moves = len(variant.bob_moves) // 2

    checkpoint = db.metadata_get_autopilot_generation_checkpoint(variant_id, crs)

    process = checkpoint is None

    job_count = 0

    for combination in itertools.product([True, False], repeat=num_fixed_moves):
        if not process:
            if combination == checkpoint:
                process = True
            continue

        job_count += 1

        job_id = str(uuid.uuid4())
        job = cache.JobOrder(
            id=job_id,
            variant_id=variant_id,
            job_type=cache.JobType.AUTOPILOT_ASSIGNMENT,
            data={"fixed": combination},
        )
        order_job(job, valkey)

        if job_count >= CHECKPOINTING_JOB_COUNT:
            process_job_status_queue(valkey, fast=False)
            db.metadata_set_autopilot_generation_checkpoint(
                variant_id,
                combination,
                crs,
            )
            job_count = 0

    print("All jobs pushed, waiting for finish")
    process_job_status_queue(valkey, fast=False)
    db.metadata_set_autopilot_generations_finished(variant_id, crs)
    print("All Autopilot assignments generated.")


def generate_assignments(  # noqa: C901
    variant_id: int,
    variant: game_variant.GameVariant,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:

    print("Generating assignments.")
    if db.metadata_get_assignment_generations_finished(variant_id, crs):
        print("Assignments were already generated, skipping.")
        return

    min_autopilot_number = db.metadata_get_min_autopilot_number(variant_id, crs)
    if min_autopilot_number is None:
        raise RuntimeError("Tried to fetch the minimum Autopilot number, but it's not set!")

    checkpoint = db.metadata_get_assignment_generation_checkpoint(variant_id, crs) or {}

    current_number = checkpoint.get("current-number", min_autopilot_number - variant.num_buckets)

    fixed_bucket_count = variant.num_buckets // 2

    job_count = 0
    process = checkpoint is None or not checkpoint

    while (
        current_number
        < min_autopilot_number + variant.num_buckets + ASSIGNMENT_GENERATION_AUTOPILOT_ADDITION
    ):
        for sum_first_buckets in range(1, current_number):
            for first_buckets in tools.distribute(sum_first_buckets, fixed_bucket_count):
                if not process:
                    # Checkpointing: Until the checkpoint itself was reached,
                    # continue.
                    if current_number < checkpoint["current-number"]:
                        continue
                    if sum_first_buckets < checkpoint["sum-first-buckets"]:
                        continue
                    if tuple(first_buckets) == tuple(checkpoint["contents-first-buckets"]):
                        # We have reached the last processed checkpoint. Continue
                        # with the next possibility.
                        process = True
                    continue
                job_count += 1
                job_id = str(uuid.uuid4())
                job = cache.JobOrder(
                    id=job_id,
                    variant_id=variant_id,
                    job_type=cache.JobType.ASSIGNMENT_GENERATOR,
                    data={
                        "fixed": first_buckets,
                        "total_num_pebbles": current_number,
                    },
                )
                order_job(job, valkey, second_queue=True)

                if job_count >= CHECKPOINTING_JOB_COUNT:
                    process_job_status_queue(valkey, fast=False)
                    db.metadata_set_assignment_generation_checkpoint(
                        variant_id,
                        {
                            "current-number": current_number,
                            "sum-first-buckets": sum_first_buckets,
                            "contents-first-buckets": first_buckets,
                        },
                        crs,
                    )

        current_number += 1

    print("All jobs pushed, waiting for finish")
    process_job_status_queue(valkey, fast=False)
    db.metadata_set_assignment_generations_finished(variant_id, crs)
    print("Assignment generation done.")


def run_recursive_computations(
    variant_id: int,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Do recusive computation on all assignments that do not
    have a known winner yet. Re-run this job as long as
    nothing changes.
    """

    print("Starting recursive computation")
    push_counter = 0

    while True:
        new_pushed = 0

        # Push all relevant jobs
        for assignment_id in db.assignments_with_unknown_winner(variant_id, crs):
            new_pushed += 1
            job_id = str(uuid.uuid4())
            job = cache.JobOrder(
                id=job_id,
                variant_id=variant_id,
                job_type=cache.JobType.RECURSIVE_COMPUTATION,
                data={"assignment-id": assignment_id},
            )
            order_job(job, valkey)

        # Wait for all relevant jobs to finish
        process_job_status_queue(valkey, fast=False)

        print(f"Processed {new_pushed} assignments")

        # Check if a different number of jobs was pushed twice
        if new_pushed == push_counter:
            print("Nothing changed, stopping here.")
            return
        push_counter = new_pushed


def main() -> None:

    crs = db.get_connection()
    valkey = cache.get_connection()

    #####################################
    # Get game variant from input data #
    #####################################
    input_path = pathlib.Path("variant.json")
    if len(sys.argv) > 1:
        input_path = pathlib.Path(sys.argv[1])

    if not input_path.exists():
        raise RuntimeError(
            "Please provide a proper input file, or generate "
            "a variant.json file in the working directory",
        )

    variant_raw = json.loads(input_path.read_text())
    variant = game_variant.GameVariant.from_dict(variant_raw)
    if not variant.validate():
        raise ValueError("Game variant seems to be invalid!")

    #############################
    # Setup game variant in DB #
    #############################
    db.setup_metadata_table(crs)
    print("Metadata table setup done.")

    variant_id = db.get_game_variant_id(variant, crs)
    if variant_id is None:
        print("Variant not found in DB, adding it")
        variant_id = db.add_game_variant(
            variant,
            description=variant_raw.get("description"),
            crs=crs,
        )
    print(f"Game variant has ID {variant_id} in DB")

    db.setup_game_variant_table(crs, variant, variant_id)
    print("Metadata setup done, ready to compute.")

    ################################
    # Compute equivalent variants #
    ################################
    compute_equivalent_variants(variant_id, variant, crs, valkey)
    compute_bucket_distances(variant_id, variant, crs, valkey)

    compute_min_autopilot_number(variant_id, variant, crs)

    generate_autopilot_variants(variant_id, variant, crs, valkey)

    generate_assignments(variant_id, variant, crs, valkey)

    run_recursive_computations(variant_id, crs, valkey)

    print("My job is done, now it's on you. Bye!")


if __name__ == "__main__":
    main()

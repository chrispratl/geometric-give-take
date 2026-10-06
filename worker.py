import itertools

import cache
import db
import decision_algorithms
import game_variant
import recursive_computation
import tools


def send_processing_status(
    job: cache.JobOrder,
    valkey: cache.Valkey,
    *,
    finished: bool = False,
) -> None:
    """
    Send an "alive" request to the manager
    If `finished`, send the corresponding finished message.
    """

    cache.push_job_status(
        cache.JobProcessStatusPush(
            id=job.id,
            status=cache.JobProcessStatus.FINISHED if finished else cache.JobProcessStatus.WORKING,
            data={},
        ),
        db=valkey,
    )


def process_job_equivalent_variants_computation(
    job: cache.JobOrder,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:

    print(f"Processing equivalent computation job with ID {job.id}")
    send_processing_status(job, valkey)

    variant = tools.get_variant_or_fail(job.variant_id, crs)
    fixed_replacements = job.data["fixed"]

    remaining = [el for el in range(variant.num_buckets) if el not in fixed_replacements]

    for variable_replacement in itertools.permutations(remaining):
        full_replacement = (*fixed_replacements, *variable_replacement)

        new_variant = game_variant.permutate_variant(variant, full_replacement)

        if new_variant == variant:
            cache.send_information_to_manager(
                data={"equivalent_variant": full_replacement},
                db=valkey,
            )

        send_processing_status(job, valkey)

    send_processing_status(job, valkey, finished=True)


def process_job_distances_computer(
    job: cache.JobOrder,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:

    print(f"Processing distance computation job with ID {job.id}")
    send_processing_status(job, valkey)

    variant = tools.get_variant_or_fail(job.variant_id, crs)

    bucket0, bucket1 = job.data["fixed"]

    for potential_num in range(variant.num_buckets - 2, -1, -1):
        for possible_path in itertools.permutations(
            itertools.filterfalse(lambda x: x in job.data["fixed"], range(variant.num_buckets)),
            potential_num,
        ):
            left = []
            right = [bucket0, *possible_path]

            while right:
                left.append(right.pop(0))

                move = game_variant.find_separator(variant, left, right + [bucket1])

                if move is None:
                    break

            else:
                cache.send_information_to_manager(
                    data={"bucket0": bucket0, "bucket1": bucket1, "distance": potential_num + 1},
                    db=valkey,
                )
                send_processing_status(job, valkey, finished=True)
                return

        send_processing_status(job, valkey)

    raise RuntimeError("Consistency Check: Distance was not found, something is wrong.")


def process_job_autopilot_assignment_generator(
    job: cache.JobOrder,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Generate Autopilot assignments.
    """

    print(f"Processing Autopilot assignment job {job.id}")
    send_processing_status(job, valkey)

    variant = tools.get_variant_or_fail(job.variant_id, crs)
    base_assignment = game_variant.Assignment(variant)
    base_assignment.bucket_content = [1] * variant.num_buckets

    for bob_move_id, add in enumerate(job.data["fixed"]):
        bob_move = variant.bob_moves[bob_move_id]
        for i in range(variant.num_buckets):
            if i in bob_move.move and add or i not in bob_move.move and not add:
                base_assignment.bucket_content[i] += 1

    equivalent_variants = db.metadata_get_equivalent_variants(job.variant_id, crs)

    fixed = len(job.data["fixed"])
    for combination in itertools.product(
        [True, False],
        repeat=len(base_assignment.bob_moves) - fixed,
    ):
        assignment = game_variant.Assignment(variant)
        assignment.bucket_content = base_assignment.bucket_content.copy()
        for bob_move_id, add in enumerate(combination):
            bob_move = base_assignment.bob_moves[fixed + bob_move_id]
            for i in range(variant.num_buckets):
                if i in bob_move.move and add or i not in bob_move.move and not add:
                    assignment.bucket_content[i] += 1

        assignment = tools.normalize_assignment(assignment, equivalent_variants)
        if db.get_assignment_metadata_by_buckets(job.variant_id, assignment, crs) is None:
            assignment_id = db.add_assignment(
                job.variant_id,
                assignment,
                autopilot_variant=True,
                crs=crs,
            )
            db.update_assignment_winner(
                job.variant_id,
                assignment_id,
                game_variant.KnownWinner.ALICE,
                crs,
            )

        send_processing_status(job, valkey)

    send_processing_status(job, valkey, finished=True)


def process_job_assignment_generator(
    job: cache.JobOrder,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Generate assignments.
    """

    print(f"Processing assignment generator job {job.id}")
    send_processing_status(job, valkey)

    variant = tools.get_variant_or_fail(job.variant_id, crs)

    equivalent_variants = db.metadata_get_equivalent_variants(job.variant_id, crs)

    first_buckets = job.data["fixed"]
    remaining_pebbles = job.data["total_num_pebbles"] - sum(first_buckets)

    for distribution in tools.distribute(
        remaining_pebbles,
        variant.num_buckets - len(first_buckets),
    ):
        assignment = game_variant.Assignment(variant, [*first_buckets, *distribution])
        assignment = tools.normalize_assignment(assignment, equivalent_variants)

        ret = db.get_assignment_metadata_by_buckets(job.variant_id, assignment, crs)
        known_winner = None

        if ret is None:
            print("Generating new assignment")
            assignment_id = db.add_assignment(
                variant_id=job.variant_id,
                assignment=assignment,
                autopilot_variant=False,
                crs=crs,
            )
        else:
            assignment_id, known_winner, _, _ = ret

        if known_winner is None:
            # Inform manager about new job
            cache.push_job_status(
                cache.JobProcessStatusPush(
                    id=job.id,
                    status=cache.JobProcessStatus.PREPARED_COMPUTING_JOB,
                    data={
                        "assignment-id": assignment_id,
                        "variant-id": job.variant_id,
                    },
                ),
                valkey,
            )
        send_processing_status(job, valkey)

    send_processing_status(job, valkey, finished=True)


def process_job_assignment_computer(
    job: cache.JobOrder,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Try to determine a winner on a given assignment.
    """

    print(f"Processign assignment computing job {job.id}")
    send_processing_status(job, valkey)

    # Fetch game variant
    variant = tools.get_variant_or_fail(job.variant_id, crs)

    # Fetch assignment metadata
    assignment_id = job.data["assignment-id"]
    assignment_metadata_raw = db.get_assignment_metadata_by_id(job.variant_id, assignment_id, crs)
    if assignment_metadata_raw is None:
        raise RuntimeError(
            f"Failed to fetch metadata for assignment {job.variant_id}/{assignment_id}!",
        )
    _, winner, _, assignment_metadata = assignment_metadata_raw

    if winner is not None and not job.data.get("force", False):
        # Nothing to compute, stop.
        send_processing_status(job, valkey, finished=True)
        return

    # Fetch assignment
    assignment = tools.get_assignment_or_fail(
        job.variant_id,
        assignment_id,
        crs,
        variant=variant,
    )

    min_autopilot_pebbles = db.metadata_get_min_autopilot_number(
        variant_id=job.variant_id,
        crs=crs,
    )
    equivalent_variants = db.metadata_get_equivalent_variants(
        variant_id=job.variant_id,
        crs=crs,
    )
    bucket_distances = db.metadata_get_bucket_distances(
        variant_id=job.variant_id,
        crs=crs,
    )

    send_processing_status(job, valkey)

    for method, kwargs, metadata_extension in [
        (
            decision_algorithms.bob_simple_wins,
            {"assignment": assignment},
            {
                "bob-win": "simple-win",
            },
        ),
        (
            decision_algorithms.bob_1_2_buckets_win,
            {"assignment": assignment},
            {
                "bob-win": "1-2-buckets",
            },
        ),
        (
            decision_algorithms.bob_1d,
            {
                "assignment": assignment,
                "distances": bucket_distances,
            },
            {
                "bob-win": "1d-win",
            },
        ),
        (
            decision_algorithms.bob_extended_1d_win,
            {
                "assignment": assignment,
                "distances": bucket_distances,
            },
            {
                "bob-win": "1d-extended-win",
            },
        ),
        (
            decision_algorithms.bob_monovariant_simple,
            {"assignment": assignment, "min_autopilot_pebbles": min_autopilot_pebbles},
            {
                "bob-win": "monovariant",
                "monovariant-reason": "few-pebbles",
            },
        ),
        (
            decision_algorithms.bob_monovariant_new,
            {"assignment": assignment},
            {
                "bob-win": "monovariant",
                "monovariant-reason": "small-pair",
            },
        ),
        (
            decision_algorithms.bob_extended_monovariant,
            {"assignment": assignment},
            {
                "bob-win": "monovariant-extended",
            },
        ),
        (
            decision_algorithms.autopilot_dominating,
            {
                "assignment": assignment,
                "variant_id": job.variant_id,
                "crs": crs,
                "variant_equivalences": equivalent_variants,
            },
            {"alice-win": "autopilot-dominating"},
        ),
    ]:
        winner, metadata_reasoning = method(**kwargs)

        if winner is not None:
            assignment_metadata.update(metadata_extension)
            assignment_metadata.update(metadata_reasoning)

            db.update_assignment_metadata(
                variant_id=job.variant_id,
                assignment_id=assignment_id,
                data=assignment_metadata,
                crs=crs,
            )
            db.update_assignment_winner(
                variant_id=job.variant_id,
                assignment_id=assignment_id,
                winner=winner,
                crs=crs,
            )

            break

        send_processing_status(job, valkey)

    send_processing_status(job, valkey, finished=True)


def process_job_recursive_computation(
    job: cache.JobOrder,
    crs: db.cursor,
    valkey: cache.Valkey,
) -> None:
    """
    Run recursive computation on a given assignment
    """

    assignment_id = job.data["assignment-id"]
    print(f"Processing recursive computation job on assignment {job.variant_id}/{assignment_id}")
    send_processing_status(job, valkey)

    rc = recursive_computation.RecursiveComputer(
        variant_id=job.variant_id,
        assignment_id=assignment_id,
        crs=crs,
        interactive=False,
    )
    send_processing_status(job, valkey)
    winner = rc.compute()
    send_processing_status(job, valkey)

    if winner is not None:
        db.update_assignment_winner(job.variant_id, assignment_id, winner, crs)
        db.update_assignment_metadata(
            job.variant_id,
            assignment_id,
            {f"{winner.value}_win": "recursive"},
            crs,
        )

    send_processing_status(job, valkey, finished=True)


def main() -> None:

    crs = db.get_connection()
    valkey = cache.get_connection()

    while True:
        job = cache.fetch_job(valkey)

        if job.job_type == cache.JobType.EQUIVALENT_VARIANTS_COMPUTATION:
            process_job_equivalent_variants_computation(job, crs, valkey)
        elif job.job_type == cache.JobType.DISTANCES_COMPUTER:
            process_job_distances_computer(job, crs, valkey)
        elif job.job_type == cache.JobType.AUTOPILOT_ASSIGNMENT:
            process_job_autopilot_assignment_generator(job, crs, valkey)
        elif job.job_type == cache.JobType.ASSIGNMENT_GENERATOR:
            process_job_assignment_generator(job, crs, valkey)
        elif job.job_type == cache.JobType.ASSIGNMENT_COMPUTER:
            process_job_assignment_computer(job, crs, valkey)
        elif job.job_type == cache.JobType.RECURSIVE_COMPUTATION:
            process_job_recursive_computation(job, crs, valkey)
        else:
            raise RuntimeError(f"Unknown job Type {job.job_type}!")


if __name__ == "__main__":
    main()

import contextlib
import uuid

import cache
import db
import game_position
import recursive_computation
import tabulate
import tools

_crs = db.get_connection()
_valkey = cache.get_connection()


def get_assignment_by_id(position_id: int, assignment_id: int) -> game_position.Assignment:
    """
    Fetch an assignment from DB.
    """

    assignment = tools.get_assignment_or_fail(position_id, assignment_id, _crs)

    print(f"Assignment ID: {assignment_id}")

    winner = db.get_assignment_winner(position_id, assignment_id, _crs)
    if winner:
        print(f"Winner is known: {winner}")
    else:
        print("Winner currently unknown")

    metadata = db.get_assignment_metadata(position_id, assignment_id, _crs)
    if metadata:
        print(f"Assignment Metadata: {metadata}")

    print(f"Assignment: {assignment}")
    return assignment


def get_assignment_by_bucket_contents(
    position_id: int,
    *bucket_contents: int,
) -> game_position.Assignment:
    """
    Fetch the assignment from the database that
    has the given bucket contents
    """

    assignment_id = db.get_assignment_id_by_bucket_contents(
        position_id,
        list(bucket_contents),
        _crs,
    )

    if assignment_id is None:
        raise RuntimeError("Assignment does not exist!")

    return get_assignment_by_id(position_id, assignment_id)


def add_assignment(
    position_id: int,
    bucket_contents: list[int],
    *,
    compute: bool = True,
) -> int:
    """
    Add an assignment for a game position.

    * `compute`: If true, add a computation job to
      be picked up by running workers.

    Returns the assignment ID.
    """

    position = tools.get_position_or_fail(position_id, _crs)

    if len(bucket_contents) != position.num_buckets:
        raise ValueError(
            f"This game position expects {position.num_buckets} "
            f"buckets, but you provided only {len(bucket_contents)}!",
        )
    assignment = game_position.Assignment(position, list(bucket_contents))

    assignment_metadata = db.get_assignment_metadata_by_buckets(position_id, assignment, _crs)
    if assignment_metadata is not None:
        print("Assignment Metadata:")
        print(assignment_metadata)
        raise RuntimeError("This assignment already exists!")

    assignment_id = db.add_assignment(
        position_id,
        assignment,
        autopilot_position=False,
        crs=_crs,
    )
    print(f"Assignment created with ID {assignment_id}")

    if compute:
        push_job_assignment_computing(position_id, assignment_id)

    return assignment_id


def compute_recursively(
    position_id: int,
    assignment_id: int,
    *,
    max_depth: int = recursive_computation.RECURSION_MAX_DEPTH,
) -> recursive_computation.RecursiveComputer:
    """
    Do recursive computation on an assignment.
    Returns the recursive computation argument,
    to allow further processing.
    """

    rc = recursive_computation.RecursiveComputer(
        position_id=position_id,
        assignment_id=assignment_id,
        max_depth=max_depth,
        crs=_crs,
    )
    winner = rc.compute()

    if winner:
        print(f"Winner found: {winner}")

    return rc


def generate_children(
    position_id: int,
    rc: recursive_computation.RecursiveComputer,
    depth: int = 1,
) -> None:
    """
    Add the children of the RC instance to the database.
    """

    for i in range(1, depth + 1):
        for child in rc.layer_children[i]:
            with contextlib.suppress(RuntimeError):
                add_assignment(position_id, child.bucket_content)


def get_trace(
    position_id: int,
    assignment_id: int,
    *,
    with_reasons: bool = True,
    max_depth: int | None = None,
) -> recursive_computation.RecursiveComputer:
    """
    Compute the trace of an assignment, and print it's
    first child layer to the console.
    """

    rc = recursive_computation.RecursiveComputer(
        position_id,
        assignment_id=assignment_id,
        max_depth=max_depth or recursive_computation.RECURSION_MAX_DEPTH,
        crs=_crs,
    )

    rc.compute()
    rows = []

    def _enrich_data(
        position_id: int,
        assignment: game_position.Assignment,
        winner: game_position.KnownWinner | None,
        prefix: str,
    ) -> dict:
        """
        Helper function to correctly add data
        to a row of the output table.
        """

        ret = {
            prefix: assignment,
            f"{prefix}_id": None,
            f"{prefix}_w": winner.value if winner else None,
            f"{prefix}_reason": None,
        }

        assignment_id = db.get_assignment_id_by_bucket_contents(
            position_id,
            assignment.bucket_content,
            _crs,
        )
        ret[f"{prefix}_id"] = assignment_id

        if assignment_id is None:
            return ret

        assignment_metadata = db.get_assignment_metadata(position_id, assignment_id, _crs)
        if winner is None or assignment_metadata is None:
            # No reason to add
            pass
        elif winner.value == game_position.KnownWinner.BOB:
            reason = assignment_metadata.get("bob-win", "unknown")

            ret[f"{prefix}_reason"] = reason
        elif winner.value == game_position.KnownWinner.ALICE:
            reason = assignment_metadata.get("alice-win", "unknown")

            ret[f"{prefix}_reason"] = reason

        if not with_reasons:
            ret.pop(f"{prefix}_reason")

        return ret

    for bob_move, (left, right) in rc.computed_children[rc.base_assignment].items():
        rows.append(
            {
                "bob_move": bob_move,
                **_enrich_data(
                    position_id=position_id,
                    assignment=left,
                    winner=rc.known_winners.get(left),
                    prefix="left",
                ),
                **_enrich_data(
                    position_id=position_id,
                    assignment=right,
                    winner=rc.known_winners.get(right),
                    prefix="right",
                ),
            },
        )

    print(rc.base_assignment)
    print(tabulate.tabulate(rows, headers="keys"))

    return rc


def push_job_assignment_computing(
    position_id: int,
    assignment_id: int,
    *,
    force: bool = True,
) -> None:
    """
    Push computation job to the managers to work on.

    * `force`: If true, potential known results will be overwritten.
      Otherwise, if the winner is already
      known, nothing is computed any more.
    """

    job = cache.JobOrder(
        id=str(uuid.uuid4()),
        position_id=position_id,
        job_type=cache.JobType.ASSIGNMENT_COMPUTER,
        data={"assignment-id": assignment_id, "force": force},
    )
    cache.push_job_to_workers(job, _valkey, process_first=True)
    print(f"Computing job created with ID {job.id}")


def push_job_recursive_computation(position_id: int, assignment_id: int) -> None:
    """
    Push recursive computation job to the managers to work on.
    """

    job = cache.JobOrder(
        id=str(uuid.uuid4()),
        position_id=position_id,
        job_type=cache.JobType.RECURSIVE_COMPUTATION,
        data={"assignment-id": assignment_id},
    )
    cache.push_job_to_workers(job, _valkey, process_first=True)
    print(f"Computing job created with ID {job.id}")


if __name__ == "__main__":
    get_trace(1, 402233)

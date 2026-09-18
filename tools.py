from collections.abc import Iterator

import db
import game_position


def yield_permutated_assignments(
    assignment: game_position.Assignment,
    permutations: list[list[int]],
) -> Iterator[game_position.Assignment]:
    """
    Yield all assignments that are equivalent to this one.
    """

    for permutation in permutations:
        new = game_position.permutate_assignment(assignment, tuple(permutation))
        yield new


def normalize_assignment(
    assignment: game_position.Assignment,
    permutations: list[list[int]],
) -> game_position.Assignment:
    """
    Given an assignment, return the normalized assignment.
    That's the one that has the lexicographically smallest
    pebble vector.
    """

    best = assignment

    for new in yield_permutated_assignments(assignment, permutations):
        if tuple(new.bucket_content) < tuple(best.bucket_content):
            best = new

    return best


def distribute(num: int, length: int) -> Iterator[list[int]]:
    """
    Generate all `length`-tuples of positive
    integers that sum up to `num`.
    """

    if length < 1:
        raise ValueError("length should be a positive integer!")

    if num < length:
        # No solution exists where every entry
        # is positive.
        return

    if length == 1:
        yield [num]
        return

    for first_num in range(1, num + 1):
        for child in distribute(num - first_num, length - 1):
            yield [first_num] + child


def compute_alice_moves(
    assignment: game_position.Assignment,
    bob_move: game_position.BobMove,
    equivalent_positions: list[list[int]],
) -> tuple[game_position.Assignment, game_position.Assignment]:
    """
    Yield the two possible Alice returns when
    Bob chooses the given bob_move.
    The assignments will be normalized, based
    on the equivalent positions known.
    """

    alice_left = game_position.Assignment(
        game_position=assignment.game_position,
        bucket_content=list(assignment.bucket_content).copy(),
    )
    alice_right = game_position.Assignment(
        game_position=assignment.game_position,
        bucket_content=list(assignment.bucket_content).copy(),
    )

    bob_move.canonicalize(n=assignment.num_buckets)

    for i, num in enumerate(assignment.bucket_content):
        if i in bob_move.move:
            alice_left.bucket_content[i] = num + 1
            alice_right.bucket_content[i] = num - 1
        else:
            alice_left.bucket_content[i] = num - 1
            alice_right.bucket_content[i] = num + 1

    alice_left = normalize_assignment(alice_left, equivalent_positions)
    alice_right = normalize_assignment(alice_right, equivalent_positions)

    return alice_left, alice_right


def get_position_or_fail(position_id: int, crs: db.cursor) -> game_position.GamePosition:
    """
    Fetch a game position and return it if
    posssible. Otherwise, raise an Exception.

    This functionality is used often, so it will be
    canonicalized here.
    """

    position = db.get_game_position(position_id, crs)

    if position is None:
        raise RuntimeError(
            f"Tried to fetch game position with ID {position_id}, but it seems to not exist!",
        )

    return position


def get_assignment_or_fail(
    position_id: int,
    assignment_id: int,
    crs: db.cursor,
    *,
    position: game_position.GamePosition | None = None,
) -> game_position.Assignment:
    """
    Fetch a an assignment and return it if
    posssible. Otherwise, raise an Exception.

    This functionality is used often, so it will be
    canonicalized here.
    """

    if position is None:
        position = get_position_or_fail(position_id, crs)

    assignment_raw = db.get_assignment_by_id(
        position_id,
        assignment_id,
        position.num_buckets,
        crs,
    )

    if assignment_raw is None:
        raise RuntimeError(
            f"Tried to fetch an assignment with ID "
            f"{position_id}/{assignment_id}, but it "
            "seems to not exist!",
        )

    return game_position.Assignment(position, assignment_raw)

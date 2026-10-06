from collections.abc import Iterator

import db
import game_variant


def yield_permutated_assignments(
    assignment: game_variant.Assignment,
    permutations: list[list[int]],
) -> Iterator[game_variant.Assignment]:
    """
    Yield all assignments that are equivalent to this one.
    """

    for permutation in permutations:
        new = game_variant.permutate_assignment(assignment, tuple(permutation))
        yield new


def normalize_assignment(
    assignment: game_variant.Assignment,
    permutations: list[list[int]],
) -> game_variant.Assignment:
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
    assignment: game_variant.Assignment,
    bob_move: game_variant.BobMove,
    equivalent_variants: list[list[int]],
) -> tuple[game_variant.Assignment, game_variant.Assignment]:
    """
    Yield the two possible Alice returns when
    Bob chooses the given bob_move.
    The assignments will be normalized, based
    on the equivalent variants known.
    """

    alice_left = game_variant.Assignment(
        game_variant=assignment.game_variant,
        bucket_content=list(assignment.bucket_content).copy(),
    )
    alice_right = game_variant.Assignment(
        game_variant=assignment.game_variant,
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

    alice_left = normalize_assignment(alice_left, equivalent_variants)
    alice_right = normalize_assignment(alice_right, equivalent_variants)

    return alice_left, alice_right


def get_variant_or_fail(variant_id: int, crs: db.cursor) -> game_variant.GameVariant:
    """
    Fetch a game variant and return it if
    posssible. Otherwise, raise an Exception.

    This functionality is used often, so it will be
    canonicalized here.
    """

    variant = db.get_game_variant(variant_id, crs)

    if variant is None:
        raise RuntimeError(
            f"Tried to fetch game variant with ID {variant_id}, but it seems to not exist!",
        )

    return variant


def get_assignment_or_fail(
    variant_id: int,
    assignment_id: int,
    crs: db.cursor,
    *,
    variant: game_variant.GameVariant | None = None,
) -> game_variant.Assignment:
    """
    Fetch a an assignment and return it if
    posssible. Otherwise, raise an Exception.

    This functionality is used often, so it will be
    canonicalized here.
    """

    if variant is None:
        variant = get_variant_or_fail(variant_id, crs)

    assignment_raw = db.get_assignment_by_id(
        variant_id,
        assignment_id,
        variant.num_buckets,
        crs,
    )

    if assignment_raw is None:
        raise RuntimeError(
            f"Tried to fetch an assignment with ID "
            f"{variant_id}/{assignment_id}, but it "
            "seems to not exist!",
        )

    return game_variant.Assignment(variant, assignment_raw)

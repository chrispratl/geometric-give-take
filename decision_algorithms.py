"""
Algorithms that may decide a winner on an assignment
"""

import itertools

import db
import game_variant
import tools
from game_variant import KnownWinner


def bob_simple_wins(assignment: game_variant.Assignment) -> tuple[KnownWinner | None, dict]:
    """
    Simple wins: Is there a zero? Are there two
    buckets with only one pebble?
    """

    if min(assignment.bucket_content) <= 0:
        return KnownWinner.BOB, {"reason": "contains-zero"}

    if assignment.bucket_content.count(1) > 1:
        return KnownWinner.BOB, {"reason": "multiple-ones"}

    return None, {}


def bob_1_2_buckets_win(assignment: game_variant.Assignment) -> tuple[KnownWinner | None, dict]:
    """
    1-2-Buckets win for Bob
    """

    indices_1 = [i for i, val in enumerate(assignment.bucket_content) if val == 1]
    indices_2 = [i for i, val in enumerate(assignment.bucket_content) if val == 2]  # noqa: PLR2004

    if not indices_1 or not indices_2:
        # Algorithm not applicable
        return None, {}

    for idx_1, idx_2 in itertools.product(indices_1, indices_2):
        # Find all Bob moves that split these two indices

        potential_bob_moves = [
            el for el in assignment.bob_moves if not el.buckets_on_same_side(idx_1, idx_2)
        ]

        for move_1, move_2 in itertools.permutations(potential_bob_moves, 2):
            # Requirement for bucket 3: move_1 separates idx_1 from idx_3,
            # move_2 separates idx_2 from idx_3
            for idx_3 in range(assignment.num_buckets):
                if idx_3 in (idx_1, idx_2):
                    continue

                if not move_1.buckets_on_same_side(idx_1, idx_3):
                    continue
                if not move_2.buckets_on_same_side(idx_2, idx_3):
                    continue

                # Requirement satisfied!
                return KnownWinner.BOB, {
                    "bucket-indices": (idx_1, idx_2, idx_3),
                    "relevant-moves": (move_1.move, move_2.move),
                }

    return None, {}


def bob_1d(
    assignment: game_variant.Assignment,
    distances: dict[str, dict[str, int]],
) -> tuple[KnownWinner | None, dict]:
    """
    Bob 1D Algorithm
    """

    for b0 in range(assignment.num_buckets):
        for b1 in range(b0 + 1, assignment.num_buckets):
            if (
                assignment.bucket_content[b0] + assignment.bucket_content[b1]
                < distances[str(b0)][str(b1)] + 2
            ):
                return KnownWinner.BOB, {"indices": (b0, b1)}

    return None, {}


def bob_extended_1d_win(
    assignment: game_variant.Assignment,
    distances: dict[str, dict[str, int]],
) -> tuple[KnownWinner | None, dict]:
    """
    Use the extended win algorithm to
    determine even more Bob Win cases.
    """

    for quad in itertools.combinations(range(assignment.num_buckets), 4):
        for idx0, idx1 in itertools.combinations(quad, 2):
            idx2, idx3 = [el for el in quad if el not in (idx0, idx1)]

            dist01 = distances[str(idx0)][str(idx1)]
            dist23 = distances[str(idx2)][str(idx3)]

            if assignment.bucket_content[idx0] + assignment.bucket_content[idx1] >= dist01 + 4:
                continue
            if assignment.bucket_content[idx2] + assignment.bucket_content[idx3] >= dist23 + 4:
                continue

            # Finally, check if they can be split accordingly
            if (
                game_variant.find_separator(assignment.game_variant, [idx0, idx1], [idx2, idx3])
                is not None
            ):
                return KnownWinner.BOB, {
                    "indices": [[idx0, idx1], [idx2, idx3]],
                }

    return None, {}


def bob_monovariant_simple(
    assignment: game_variant.Assignment,
    min_autopilot_pebbles: int,
) -> tuple[KnownWinner | None, dict]:
    """
    Simple Monovariant: If there are less than min-Autopilot
    pebbles then Bob has a winning strategy.
    """

    if sum(assignment.bucket_content) < min_autopilot_pebbles:
        return KnownWinner.BOB, {}

    return None, {}


def bob_monovariant_new(
    assignment: game_variant.Assignment,
) -> tuple[KnownWinner | None, dict]:
    """
    Check if the Bob Monovariant algorithm
    is applicable
    """

    for i, j in itertools.combinations(range(assignment.num_buckets), r=2):
        if (
            assignment.bucket_content[i] + assignment.bucket_content[j]
            < game_variant.count_number_of_lines_between(assignment.game_variant, i, j) + 2
        ):
            return KnownWinner.BOB, {"monovariant-tuple": [i, j]}

    return None, {}


def bob_extended_monovariant(
    assignment: game_variant.Assignment,
) -> tuple[KnownWinner | None, dict]:
    """
    Use the extended Monovariant algorithm.
    """

    for a, b, c, d in itertools.product(range(assignment.num_buckets), repeat=4):
        # Only consider cases where these four are actually different
        if len({a, b, c, d}) < 4:  # noqa: PLR2004
            continue

        if (
            assignment.bucket_content[a] + assignment.bucket_content[b]
            >= game_variant.count_number_of_lines_between(assignment.game_variant, a, b) + 4
        ):
            continue
        if (
            assignment.bucket_content[c] + assignment.bucket_content[d]
            >= game_variant.count_number_of_lines_between(assignment.game_variant, c, d) + 4
        ):
            continue

        # Determine if we can actually separate
        # a and b from c and d

        if game_variant.find_separator(assignment.game_variant, [a, b], [c, d]) is not None:
            return KnownWinner.BOB, {"indices": [[a, b], [c, d]]}

    return None, {}


def autopilot_dominating(
    assignment: game_variant.Assignment,
    variant_id: int,
    crs: db.cursor,
    variant_equivalences: list[list[int]],
) -> tuple[KnownWinner | None, dict]:
    """
    Check if some Autopilot variant is being dominated.
    """

    for potential_dominated in db.find_potential_dominated_autopilot_win(
        variant_id=variant_id,
        bucket_contents=assignment.bucket_content,
        crs=crs,
    ):
        potential_dominated_assignment = game_variant.Assignment(
            game_variant=assignment.game_variant,
        )
        potential_dominated_assignment.bucket_content = [*potential_dominated[1:]]

        for equivalent in tools.yield_permutated_assignments(
            potential_dominated_assignment,
            variant_equivalences,
        ):
            if all(
                left >= right
                for left, right in zip(
                    assignment.bucket_content,
                    equivalent.bucket_content,
                    strict=True,
                )
            ):
                return KnownWinner.ALICE, {
                    "dominated-variant-id": potential_dominated[0],
                    "dominated-autopilot-varianting": equivalent.bucket_content,
                }

    return None, {}

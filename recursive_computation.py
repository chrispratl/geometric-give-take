"""
Full recursive computation implementation.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from tqdm import tqdm

import db
import decision_algorithms
import game_variant
import tools

RECURSION_MAX_DEPTH = 3


@dataclass
class Trace:
    type: str
    bob_move: game_variant.BobMove | None


def find_winner_in_db(
    variant_id: int,
    assignment: game_variant.Assignment,
    crs: db.cursor,
) -> None | game_variant.KnownWinner:
    """
    Check if a winner is known in the database
    """

    metadata = db.get_assignment_metadata_by_buckets(variant_id, assignment, crs)

    if metadata is None:
        return None

    winner_raw = metadata[1]
    if winner_raw is None:
        return None
    return game_variant.KnownWinner(winner_raw.strip())


class RecursiveComputer:
    """
    Arguments:
    * `variant_id`: Required, defines the variant ID
      that will be worked on
    * `assignment_id` or `assignment`: One of the two has to
      be set, defines the assignment to work on.
    * `max_depth`: The maximum depth the tree should have
    * `crs`: A database cursor
    * `interactive`: If True, then some progress bar will
      be shown; otherwise this will be quiet
    """

    def __init__(  # noqa: PLR0913
        self,
        variant_id: int,
        *,
        assignment_id: int | None = None,
        assignment: game_variant.Assignment | None = None,
        max_depth: int = RECURSION_MAX_DEPTH,
        crs: db.cursor | None = None,
        interactive: bool = True,
    ) -> None:

        self.max_depth = max_depth
        self.crs = crs or db.get_connection()

        self.variant_id = variant_id
        self.variant = db.get_game_variant(self.variant_id, crs)
        if self.variant is None:
            raise RuntimeError(f"Game variant with ID {self.variant_id} not found!")

        ################################
        # Fetch game variant metadata #
        ################################

        self.equivalent_variants = db.metadata_get_equivalent_variants(self.variant_id, self.crs)
        if self.equivalent_variants is None:
            raise RuntimeError(
                f"Unable to fetch equivalent variants for variant {self.variant_id}!",
            )

        #########################
        # Setup base assignment #
        #########################
        if assignment is not None:
            self.base_assignment = assignment
        elif assignment_id is not None:
            assignment_data_raw = db.get_assignment_by_id(
                self.variant_id,
                assignment_id,
                self.variant.num_buckets,
                self.crs,
            )

            if assignment_data_raw is None:
                raise RuntimeError(f"Assignment {self.variant_id}/{assignment_id} not found!")

            self.base_assignment = game_variant.Assignment(
                game_variant=self.variant,
                bucket_content=list(assignment_data_raw),
            )
        else:
            raise ValueError("Please either pass the assignment, or the assignment ID!")

        #######################################
        # Setup variables used in computation #
        #######################################
        self.computed_children: dict[
            game_variant.Assignment,
            dict[game_variant.BobMove, tuple[game_variant.Assignment, game_variant.Assignment]],
        ] = {}
        self.known_winners: dict[game_variant.Assignment, decision_algorithms.KnownWinner] = {}
        self.layer_children: dict[int, list[game_variant.Assignment]] = {}
        self.trace: dict[game_variant.Assignment, Trace] = {}

        self.interactive = interactive

    def iterate(self, iterator: Iterable) -> Iterator:
        """
        Iterate through a given iterator.
        If wanted, show a TQDM.
        """

        if self.interactive:
            yield from tqdm(iterator)
        else:
            yield from iterator

    def compute(self) -> None | decision_algorithms.KnownWinner:
        """
        Try to find winner for a variant by
        doing recursive computations.
        """

        self.layer_children[0] = [self.base_assignment]

        for layer in range(1, self.max_depth):
            self.layer_children[layer] = self.layer_children.get(layer, [])
            for assignment in self.iterate(self.layer_children[layer - 1]):
                if assignment in self.known_winners:
                    continue
                if assignment not in self.computed_children:
                    self.generate_children(assignment)

                # Setup next layer
                for children in self.computed_children[assignment].values():
                    self.layer_children[layer].extend(children)

            for prev_layer in range(layer - 1, -1, -1):
                found_change = self.check_layer(prev_layer)

                if not found_change:
                    # There will also not be a change in the next layer, so let's not continue here.
                    break

            if self.known_winners.get(self.base_assignment):
                # Done!
                return self.known_winners[self.base_assignment]

        return None

    def generate_children(self, assignment: game_variant.Assignment) -> None:
        """
        Compute all children for an assignment,
        and store them.
        Also, check if we know the winners for
        the variants, and add them to the correct
        dict, if so.
        Also add the children to the next child layer.
        """

        ret = self.computed_children.get(assignment, {})

        for move in assignment.bob_moves:
            ret[move] = tools.compute_alice_moves(assignment, move, self.equivalent_variants)

            for child_pos in ret[move]:
                if child_pos in self.known_winners:
                    # No need for a re-search
                    continue

                if min(child_pos.bucket_content) <= 0:
                    self.known_winners[child_pos] = decision_algorithms.KnownWinner.BOB
                    continue

                winner = find_winner_in_db(
                    self.variant_id,
                    child_pos,
                    self.crs,
                )

                if winner is not None:
                    self.known_winners[child_pos] = winner

        self.computed_children[assignment] = ret

    def check_layer(self, layer: int) -> bool:
        """
        Check if we can say something about a layer, based on what
        we know about the later layers.
        Returns True if something changed, False otherwise.
        """

        changed = False

        # Bob recursive win?
        # An assignment is a Bob win if there is some Bob move
        # for which both assignment-children are Bob wins.
        for assignment in self.layer_children[layer]:
            if self.known_winners.get(assignment):
                # Already known, no check needed
                continue

            for move, (left, right) in self.computed_children[assignment].items():
                if (
                    self.known_winners.get(left) == decision_algorithms.KnownWinner.BOB
                    and self.known_winners.get(right) == decision_algorithms.KnownWinner.BOB
                ):
                    self.known_winners[assignment] = decision_algorithms.KnownWinner.BOB
                    self.trace[assignment] = Trace(type="Recursive", bob_move=move)
                    changed = True
                    break

        # Alice recursive win?
        # An assignment is an Alice win if for every move
        # that Bob can make, one of the children is an Alice
        # move.
        for assignment in self.layer_children[layer]:
            if self.known_winners.get(assignment):
                continue

            alice_can_win = True
            for left, right in self.computed_children[assignment].values():
                if not (
                    self.known_winners.get(left) == decision_algorithms.KnownWinner.ALICE
                    or self.known_winners.get(right) == decision_algorithms.KnownWinner.ALICE
                ):
                    alice_can_win = False
                    break

            if alice_can_win:
                self.known_winners[assignment] = decision_algorithms.KnownWinner.ALICE
                self.trace[assignment] = Trace(type="recursive", bob_move=None)
                changed = True

        return changed

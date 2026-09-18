"""
Full recursive computation implementation.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import db
import decision_algorithms
import game_position
import tools
from tqdm import tqdm

RECURSION_MAX_DEPTH = 3


@dataclass
class Trace:
    type: str
    bob_move: game_position.BobMove | None


def find_winner_in_db(
    position_id: int,
    assignment: game_position.Assignment,
    crs: db.cursor,
) -> None | game_position.KnownWinner:
    """
    Check if a winner is known in the database
    """

    metadata = db.get_assignment_metadata_by_buckets(position_id, assignment, crs)

    if metadata is None:
        return None

    winner_raw = metadata[1]
    if winner_raw is None:
        return None
    return game_position.KnownWinner(winner_raw.strip())


class RecursiveComputer:
    """
    Arguments:
    * `position_id`: Required, defines the position ID
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
        position_id: int,
        *,
        assignment_id: int | None = None,
        assignment: game_position.Assignment | None = None,
        max_depth: int = RECURSION_MAX_DEPTH,
        crs: db.cursor | None = None,
        interactive: bool = True,
    ) -> None:

        self.max_depth = max_depth
        self.crs = crs or db.get_connection()

        self.position_id = position_id
        self.position = db.get_game_position(self.position_id, crs)
        if self.position is None:
            raise RuntimeError(f"Game position with ID {self.position_id} not found!")

        ################################
        # Fetch game position metadata #
        ################################

        self.equivalent_positions = db.metadata_get_equivalent_positions(self.position_id, self.crs)
        if self.equivalent_positions is None:
            raise RuntimeError(
                f"Unable to fetch equivalent positions for position {self.position_id}!",
            )

        #########################
        # Setup base assignment #
        #########################
        if assignment is not None:
            self.base_assignment = assignment
        elif assignment_id is not None:
            assignment_data_raw = db.get_assignment_by_id(
                self.position_id,
                assignment_id,
                self.position.num_buckets,
                self.crs,
            )

            if assignment_data_raw is None:
                raise RuntimeError(f"Assignment {self.position_id}/{assignment_id} not found!")

            self.base_assignment = game_position.Assignment(
                game_position=self.position,
                bucket_content=list(assignment_data_raw),
            )
        else:
            raise ValueError("Please either pass the assignment, or the assignment ID!")

        #######################################
        # Setup variables used in computation #
        #######################################
        self.computed_children: dict[
            game_position.Assignment,
            dict[game_position.BobMove, tuple[game_position.Assignment, game_position.Assignment]],
        ] = {}
        self.known_winners: dict[game_position.Assignment, decision_algorithms.KnownWinner] = {}
        self.layer_children: dict[int, list[game_position.Assignment]] = {}
        self.trace: dict[game_position.Assignment, Trace] = {}

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
        Try to find winner for a position by
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

    def generate_children(self, assignment: game_position.Assignment) -> None:
        """
        Compute all children for an assignment,
        and store them.
        Also, check if we know the winners for
        the positions, and add them to the correct
        dict, if so.
        Also add the children to the next child layer.
        """

        ret = self.computed_children.get(assignment, {})

        for move in assignment.bob_moves:
            ret[move] = tools.compute_alice_moves(assignment, move, self.equivalent_positions)

            for child_pos in ret[move]:
                if child_pos in self.known_winners:
                    # No need for a re-search
                    continue

                if min(child_pos.bucket_content) <= 0:
                    self.known_winners[child_pos] = decision_algorithms.KnownWinner.BOB
                    continue

                winner = find_winner_in_db(
                    self.position_id,
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

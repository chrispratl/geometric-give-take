"""
Representation of a Line, a Game Variant
and an Assignment. Additionally, a set of helper
methods on them.
"""

from enum import Enum
from typing import Self


class KnownWinner(Enum):
    ALICE = "alice"
    BOB = "bob"


class BobMove:
    """
    Representation of a Bob Move.
    Note: Argument `n` is optional and not part
    of the
    """

    def __init__(self, move: list[int] | tuple[int, ...], n: int | None = None) -> None:
        self.move = tuple(sorted(move))
        self.n = n

    def canonicalize(self, n: int | None = None) -> None:
        """
        Canonicalize myself.
        """

        if n is None and self.n is None:
            raise ValueError("Canonical form cannot be computed without a number of buckets!")
        if self.n is None:
            self.n = n

        self.move = self.canonical()

    def canonical(self, n: int | None = None) -> tuple[int, ...]:
        """
        Canonical representation of the move,
        given the number of buckets: The choice that
        contains bucket 0.

        Note that this is self-overwriting: The move will
        be overwritten by the canonical move on first run.
        """

        if n is None and self.n is None:
            raise ValueError("Canonical form cannot be computed without a number of buckets!")
        if self.n is None:
            self.n = n
        if self.n is None:
            raise RuntimeError("Unable to determine number of buckets!")

        if 0 in self.move:
            return self.move

        self.move = tuple(el for el in range(self.n) if el not in self.move)
        return self.move

    def validate(self, n: int | None = None) -> bool:
        """
        Validate that this move is valid for the
        known number of buckets
        """

        if n is None and self.n is None:
            raise ValueError("Number of buckets is required!")
        if self.n is None:
            self.n = n
        if self.n is None:
            raise RuntimeError("Unable to determine number of buckets!")

        for element in self.move:  # noqa: SIM110
            if not 0 <= element <= self.n - 1:
                return False

        return True

    def buckets_on_same_side(self, num1: int, num2: int) -> bool:
        """
        Check if the two given buckets are on the same side
        of the move.
        """

        return (num1 in self.move and num2 in self.move) or (
            num1 not in self.move and num2 not in self.move
        )

    def all_buckets_on_same_side(self, *buckets: int) -> bool:
        """
        Test if all buckets are on the same side.
        Use transitivity here: Check if all buckets are on
        the same side as the first one.
        """

        for i in range(1, len(buckets)):
            if not self.buckets_on_same_side(buckets[0], buckets[i]):
                return False

        return True

    def to_dict(self) -> tuple:
        """
        Create a JSON representation.
        """

        return self.move

    @classmethod
    def from_dict(cls, data: tuple[int]) -> Self:
        """
        Create a Bob move from dict.
        """
        return cls(data)

    def __hash__(self) -> int:
        self.canonicalize()
        return hash((self.n, self.move))

    def __eq__(self, other: object) -> bool:

        if type(other) is not type(self):
            return False

        if self.n is None and other.n is None:
            raise ValueError("Comparision is only possible with defined number of buckets!")
        if self.n is None:
            self.n = other.n

        return self.canonical(self.n) == other.canonical(self.n)

    def __lt__(self, other: Self) -> bool:

        if type(other) is not type(self):
            raise ValueError("Cannot compare a Bob Move to anything else except for a Bob Move.")

        if self.n is None and other.n is None:
            raise ValueError("Comparision is only possible with defined number of buckets!")
        if self.n is None:
            self.n = other.n

        if self == other:
            return False

        if len(self.move) < len(other.move):
            return True
        if len(other.move) < len(self.move):
            return False

        # Same length
        for val_me, val_other in zip(self.move, other.move, strict=True):
            if val_me > val_other:
                return False
            if val_me < val_other:
                return True

        # Since I have already considered the case "==" before,
        # I do not want to reach here.
        raise ValueError("Seems that i fucked up in the '<' relation of Bob Moves.")

    def __gt__(self, other: Self) -> bool:
        return self < other

    def __leq__(self, other: Self) -> bool:
        return self < other or self == other

    def __geq__(self, other: Self) -> bool:
        return self > other or self == other

    def __repr__(self) -> str:
        if self.n:
            return str(self.canonical())
        return str(self.move)

    def __str__(self) -> str:
        return repr(self)

    def __contains__(self, num: int) -> bool:
        return num in self.move


class GameVariant:
    """
    Game Varinant Base Class.
    """

    def __init__(self, num_buckets: int, bob_moves: list[BobMove]) -> None:

        self.num_buckets = num_buckets
        self.bob_moves = bob_moves

        self.canonicalized: bool = False

    def __hash__(self) -> int:
        self.canonicalize()
        return hash([self.num_buckets, *self.bob_moves])

    def validate(self) -> bool:
        """
        Validate that the game variant is valid.
        Currently, this mainly means: Every defined
        Bob move has to be an actually possible
        move.
        """

        for bob_move in self.bob_moves:
            if not bob_move.validate(self.num_buckets):
                return False

        # Verify that all moves are actually different
        for index_left, element_left in enumerate(self.bob_moves):
            for index_right in range(index_left + 1, len(self.bob_moves)):
                if element_left == self.bob_moves[index_right]:
                    return False

        return True

    def canonicalize(self) -> None:
        """
        Canonicalize the game variant.
        Will only happen once to prevent
        too much double computing.

        Canonicalizing means the following:
        * Bucket 0 will be present in the representation
          of the Bob Move. That way, there is also a clear
          canonical representation of the Bob Move.
        """

        if self.canonicalized:
            return

        if not self.validate():
            raise ValueError("Unable to normalize; the game variant is not valid!")

        new_moves: list[BobMove] = []

        for bob_move in self.bob_moves:
            bob_move.canonicalize()
            new_moves.append(bob_move)

        # I may also canonicalize where bucket 0 is (e.g outer convex hull will be the
        # first buckets, in ordered, or so). Not yet sure what makes sense and how to
        # do it.

        self.bob_moves = sorted(new_moves)
        self.canonicalized = True

    def to_dict(self) -> dict:
        """
        Create a dictionary from self.
        """
        self.canonicalize()
        return {
            "num_buckets": self.num_buckets,
            "bob_moves": [el.to_dict() for el in self.bob_moves],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """
        Parse a variant from dict.
        """
        return cls(
            num_buckets=data["num_buckets"],
            bob_moves=[BobMove.from_dict(el) for el in data["bob_moves"]],
        )

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __str__(self) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return False

        self.canonicalize()
        other.canonicalize()

        if self.num_buckets != other.num_buckets:
            return False

        if len(self.bob_moves) != len(other.bob_moves):
            return False

        for bob_move in self.bob_moves:
            bob_move.n = self.num_buckets
            if bob_move not in other.bob_moves:
                return False

        return True


def permutate_variant(variant: GameVariant, permutation: tuple[int, ...]) -> GameVariant:
    """
    Create a permutation of the given Game Variant
    by replacing all pebbles with the one given
    in the permutation.
    """

    if sorted(permutation) != list(range(variant.num_buckets)):
        raise ValueError("Incorrect input for permutation!")

    ret = GameVariant(num_buckets=variant.num_buckets, bob_moves=[])

    for bob_move in variant.bob_moves:
        new_move = BobMove(move=[permutation[i] for i in bob_move.move])
        ret.bob_moves.append(new_move)

    return ret


class Assignment:
    """
    An actual assignment, where buckets receive
    pebbles.
    """

    def __init__(
        self,
        game_variant: GameVariant,
        bucket_content: list[int] | None = None,
    ) -> None:
        self.game_variant = game_variant

        if bucket_content is not None and len(bucket_content) != game_variant.num_buckets:
            raise ValueError("Incorrect input for bucket_content!")

        self.bucket_content: list[int] = bucket_content or [0] * self.game_variant.num_buckets

    @property
    def num_buckets(self) -> int:
        """
        Number of buckets - Just inherit from game_variant
        """
        return self.game_variant.num_buckets

    @property
    def bob_moves(self) -> list[BobMove]:
        """
        Bob Moves - Just inherit from game_variant
        """
        return self.game_variant.bob_moves

    def __repr__(self) -> str:
        return str(self.bucket_content)


def find_separator(
    game_variant: GameVariant,
    left: list[int],
    right: list[int],
) -> None | BobMove:
    """
    Search for a BobMove that separates the left
    list of buckets from the right list of buckets.
    """

    if not left or not right:
        raise ValueError("Cannot find a separator to an empty side!")

    if (
        min(left) < 0
        or min(right) < 0
        or max(left) >= game_variant.num_buckets
        or max(right) >= game_variant.num_buckets
    ):
        raise ValueError("Buckets needs to be in the correct range!")

    if set(left).intersection(set(right)):
        raise ValueError("The two sides are not disjoint!")

    for move in game_variant.bob_moves:
        # left and right actually different?
        if move.buckets_on_same_side(left[0], right[0]):
            continue
        if not move.all_buckets_on_same_side(*left):
            continue
        if not move.all_buckets_on_same_side(*right):
            continue

        # All left buckets are on one side of the move.
        # All right buckets are on one side of the move.
        # The two sides are not the same side.
        # This is a good move!
        return move

    return None


def permutate_assignment(assignment: Assignment, permutation: tuple[int, ...]) -> Assignment:
    """
    Create a permutation of the given assignment
    by replacing the base game variant, and
    moving the correct number of pebbles.
    """

    new = Assignment(
        game_variant=permutate_variant(assignment.game_variant, permutation),
    )
    new.bucket_content = [assignment.bucket_content[i] for i in permutation]

    return new


def count_number_of_lines_between(game_variant: GameVariant, a: int, b: int) -> int:
    """
    Count the number of lines between two buckets.
    """

    cnt = 0
    for bob_move in game_variant.bob_moves:
        if not bob_move.buckets_on_same_side(a, b):
            cnt += 1

    return cnt

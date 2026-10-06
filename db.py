"""
Functions for interaction with the database.
"""

import contextlib
import json
import os
from collections.abc import Iterator
from functools import lru_cache

import psycopg2
from psycopg2.extensions import cursor

import game_variant

# ruff: disable[S608]

METADATA_TABLENAME = "metadata"
VARIANT_TABLENAME_PREFIX = "variants_"


def get_connection() -> cursor:
    """
    Create a connection to the database.
    """

    cnxn = psycopg2.connect(
        dbname=os.environ.get("POSTGRES_DB", "postgres"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "db"),
        port="5432",
    )

    cnxn.autocommit = True

    return cnxn.cursor()


##################
# Metadata table #
##################


def setup_metadata_table(crs: cursor) -> None:
    """
    Setup the metadata table, if it does not exist yet.
    """

    stmt = f"""
    CREATE TABLE
    IF NOT EXISTS
    {METADATA_TABLENAME} (
        id serial not null,
        primary key (id),

        num_buckets integer NOT NULL,
        description text null,

        min_autopilot_pebbles integer null,

        representation json not null,

        equivalent_variants json,
        bucket_distances json,

        autopilot_generation_done boolean not null,
        autopilot_generation_checkpoint json null,

        assignment_generation_done boolean not null,
        assignment_generation_checkpoint json null
    )
    """

    crs.execute(stmt)


def get_game_variant_id(variant: game_variant.GameVariant, crs: cursor) -> int | None:
    """
    Iterate all available game variants and check if any of them
    is equal to this game variant. If found, yield the ID
    of the game variant.
    """

    crs.execute(f"select id, representation from {METADATA_TABLENAME}")

    for potential_id, representation in crs.fetchall():
        other_variant = game_variant.GameVariant.from_dict(representation)
        if variant == other_variant:
            return potential_id

    return None


def add_game_variant(
    variant: game_variant.GameVariant,
    description: str | None,
    crs: cursor,
) -> int:
    """
    Add a game variant to the database.
    """

    crs.execute(
        f"""
            INSERT INTO {METADATA_TABLENAME}
            (
                num_buckets,
                representation,
                autopilot_generation_done,
                assignment_generation_done,
                description
            )
            VALUES (%s, %s, %s, %s, %s)
            returning id
        """,
        (variant.num_buckets, json.dumps(variant.to_dict()), False, False, description),
    )

    ret = crs.fetchone()
    if ret is None:
        raise RuntimeError("Something failed when setting up a new variant!")
    return ret[0]


def metadata_update_variant_metadata(
    variant_id: int,
    data: dict,
    crs: cursor,
) -> None:
    """
    Update the metadata of an assignment, based
    on the given dictionary.
    """

    crs.execute(
        """
            SELECT metadata
            FROM metadata
            WHERE id = %s
        """,
        (variant_id,),
    )
    data_raw = crs.fetchone()

    if data_raw is None:
        raise RuntimeError("Assignment does not exist!")

    data_db = json.loads(data_raw[0])
    data_db.update(data)

    crs.execute(
        """
        UPDATE metadata
        SET metadata = %s
        WHERE id = %s
        """,
        (json.dumps(data_db), variant_id),
    )


@lru_cache(maxsize=2)
def get_game_variant(game_variant_id: int, crs: cursor) -> game_variant.GameVariant | None:
    """
    Return the game variant that has the given ID
    Note: The game variant does not change, so in order to
    decrease the number of requests to the database, this method
    can be cached. Storing up to two elements should be enough
    in general.
    """

    crs.execute(
        f"SELECT representation from {METADATA_TABLENAME} where ID = %s",
        (game_variant_id,),
    )

    raw = crs.fetchone()
    if raw is None:
        return None

    variant = game_variant.GameVariant.from_dict(raw[0])
    variant.canonicalize()
    return variant


@lru_cache(maxsize=2)
def metadata_get_bucket_distances(variant_id: int, crs: cursor) -> dict[str, dict[str, int]]:
    crs.execute(
        f"""
            select bucket_distances
            from {METADATA_TABLENAME}
            where id = %s
        """,
        (variant_id,),
    )

    ret = crs.fetchone()

    if ret is None:
        raise RuntimeError("Equivalent variants were not (yet) set!")

    return ret[0]


def metadata_set_bucket_distances(
    variant_id: int,
    bucket_distances: dict[int, dict[int, int]],
    crs: cursor,
) -> None:

    crs.execute(
        f"""
        UPDATE {METADATA_TABLENAME}
        SET bucket_distances = %s
        WHERE id = %s
        """,
        (json.dumps(bucket_distances), variant_id),
    )


@lru_cache(maxsize=2)
def metadata_get_equivalent_variants(variant_id: int, crs: cursor) -> list[list[int]]:
    crs.execute(
        f"""
            select equivalent_variants
            from {METADATA_TABLENAME}
            where id = %s
        """,
        (variant_id,),
    )

    ret = crs.fetchone()

    if ret is None:
        raise RuntimeError("Equivalent variants were not (yet) set!")

    return ret[0]


def metadata_set_equivalent_variants(
    variant_id: int,
    equivalent_variants: list[tuple[int, ...]],
    crs: cursor,
) -> None:

    crs.execute(
        f"""
        UPDATE {METADATA_TABLENAME}
        SET equivalent_variants = %s
        WHERE id = %s
        """,
        (json.dumps(equivalent_variants), variant_id),
    )


@lru_cache(maxsize=2)
def metadata_get_min_autopilot_number(variant_id: int, crs: cursor) -> int | None:
    """
    Return the minimum Autopilot number for a game variant.
    """

    crs.execute(
        f"""
        select min_autopilot_pebbles
        from {METADATA_TABLENAME}
        where id = %s
        """,
        (variant_id,),
    )

    ret = crs.fetchone()
    if ret is None:
        return None

    return ret[0]


def metadata_set_min_autopilot_number(variant_id: int, min_autopilot: int, crs: cursor) -> None:
    """
    Set the minimum Autopilot number for a game variant.
    """

    crs.execute(
        f"""
        update {METADATA_TABLENAME}
        set min_autopilot_pebbles = %s
        where id = %s
        """,
        (
            min_autopilot,
            variant_id,
        ),
    )


def metadata_set_autopilot_generation_checkpoint(
    variant_id: int,
    checkpoint: tuple[bool, ...],
    crs: cursor,
) -> None:
    """
    Set a checkpoint for the Autopilot generation job.
    """

    crs.execute(
        """
        UPDATE metadata
        SET autopilot_generation_checkpoint = %s
        WHERE id = %s
        """,
        (json.dumps(checkpoint), variant_id),
    )


def metadata_get_autopilot_generation_checkpoint(
    variant_id: int,
    crs: cursor,
) -> tuple[bool, ...] | None:
    """
    Fetch the current Autopilot generation checkpoint
    """

    crs.execute(
        """
        SELECT autopilot_generation_checkpoint
        FROM metadata
        WHERE id = %s
        """,
        (variant_id,),
    )

    data_raw = crs.fetchone()

    if data_raw is None or data_raw[0] is None:
        return None

    return tuple(data_raw[0])


def metadata_get_autopilot_generations_finished(variant_id: int, crs: cursor) -> bool:
    """
    Check if the Autopilot generation was already finished
    """

    crs.execute(
        """
            SELECT autopilot_generation_done
            FROM metadata
            WHERE id = %s
        """,
        (variant_id,),
    )

    data_raw = crs.fetchone()

    if data_raw is None:
        return False
    return data_raw[0] is True


def metadata_set_autopilot_generations_finished(variant_id: int, crs: cursor) -> None:
    """
    Indicate the Autopilot generation as finished.
    """

    crs.execute(
        """
            UPDATE metadata
            SET
                autopilot_generation_done = true,
                autopilot_generation_checkpoint = null
            WHERE id = %s
        """,
        (variant_id,),
    )


def metadata_set_assignment_generation_checkpoint(
    variant_id: int,
    checkpoint: dict,
    crs: cursor,
) -> None:
    """
    Set a checkpoint for the Autopilot generation job.
    """

    crs.execute(
        """
        UPDATE metadata
        SET assignment_generation_checkpoint = %s
        WHERE id = %s
        """,
        (json.dumps(checkpoint), variant_id),
    )


def metadata_get_assignment_generation_checkpoint(
    variant_id: int,
    crs: cursor,
) -> dict | None:
    """
    Fetch the current Autopilot generation checkpoint
    """

    crs.execute(
        """
        SELECT assignment_generation_checkpoint
        FROM metadata
        WHERE id = %s
        """,
        (variant_id,),
    )

    data_raw = crs.fetchone()

    if data_raw is None or data_raw[0] is None:
        return None

    return data_raw[0]


def metadata_get_assignment_generations_finished(variant_id: int, crs: cursor) -> bool:
    """
    Check if the assignment generation was already finished
    """

    crs.execute(
        """
            SELECT assignment_generation_done
            FROM metadata
            WHERE id = %s
        """,
        (variant_id,),
    )

    data_raw = crs.fetchone()

    if data_raw is None:
        return False
    return data_raw[0] is True


def metadata_set_assignment_generations_finished(variant_id: int, crs: cursor) -> None:
    """
    Indicate the assignment generation as finished.
    """

    crs.execute(
        """
            UPDATE metadata
            SET
                assignment_generation_done = true,
                assignment_generation_checkpoint = null
            WHERE id = %s
        """,
        (variant_id,),
    )


######################
# Game Variant Table #
######################


def get_variant_tablename(variant_id: int) -> str:
    """
    Get the variant data tablename for a given variant ID.
    This is used in multiple variants, and should remain
    consistent, so the actual name is created in an
    own method.
    """

    return f"{VARIANT_TABLENAME_PREFIX}{variant_id}"


def setup_game_variant_table(
    crs: cursor,
    variant: game_variant.GameVariant,
    variant_id: int,
) -> None:
    """
    Create a table for one specific variant.
    """

    columns = ["id SERIAL PRIMARY KEY"]
    numbers = [f"num{k} INT NOT NULL" for k in range(variant.num_buckets)]
    columns.extend(numbers)
    numbers_sorted = [f"sorted{k} INT NOT NULL" for k in range(variant.num_buckets)]
    columns.extend(numbers_sorted)

    columns.append("winner character(5)")
    columns.append("autopilot_variant boolean not null")
    columns.append("metadata json null")

    crs.execute(f"""
        CREATE TABLE
        IF NOT EXISTS
        {get_variant_tablename(variant_id)}
        ({", ".join(columns)})
    """)

    # Create indices
    with contextlib.suppress(psycopg2.errors.DuplicateObject):
        crs.execute("create extension btree_gist")
    with contextlib.suppress(psycopg2.errors.DuplicateTable):
        # Search index on buckets
        crs.execute(f"""
            CREATE INDEX
            {get_variant_tablename(variant_id)}_pos_search
            ON
            {get_variant_tablename(variant_id)}
            ({", ".join([f"num{k}" for k in range(variant.num_buckets)])})
        """)
    with contextlib.suppress(psycopg2.errors.DuplicateTable):
        # btree Index on buckets
        crs.execute(f"""
            CREATE INDEX
            {get_variant_tablename(variant_id)}_pos_search_btree
            ON
            {get_variant_tablename(variant_id)}
            ({", ".join([f"num{k}" for k in range(variant.num_buckets)])})
        """)
    with contextlib.suppress(psycopg2.errors.DuplicateTable):
        # btree index on sorted buckets
        crs.execute(f"""
            CREATE INDEX
            {get_variant_tablename(variant_id)}_sorted_btree
            ON
            {get_variant_tablename(variant_id)}
            ({", ".join([f"sorted{k}" for k in range(variant.num_buckets)])})
        """)
    with contextlib.suppress(psycopg2.errors.DuplicateTable):
        # Index on Autopilot Information
        crs.execute(f"""
            CREATE INDEX
            {get_variant_tablename(variant_id)}_autopilot
            ON
            {get_variant_tablename(variant_id)}
            (autopilot_variant)
        """)


def get_assignment_metadata_by_id(
    variant_id: int,
    assignment_id: int,
    crs: cursor,
) -> tuple[int, str | None, bool, dict] | None:
    """
    Fetch information about an assignment from the database.

    Returns:
    * Assignment ID
    * Winner
    * Autopilot variant
    * Metadata
    """

    crs.execute(
        f"""
            SELECT id, winner, autopilot_variant, metadata
            FROM {get_variant_tablename(variant_id)}
            WHERE ID = %s
        """,
        (assignment_id,),
    )

    return crs.fetchone()


def get_assignment_metadata_by_buckets(
    variant_id: int,
    assignment: game_variant.Assignment,
    crs: cursor,
) -> tuple[int, str | None, bool, dict] | None:
    """
    Fetch information about an assignment from the database.

    Returns:
    * Assignment ID
    * Winner
    * Autopilot variant
    * Metadata
    """

    stmt = f"""
        SELECT id, winner, autopilot_variant, metadata
        FROM {get_variant_tablename(variant_id)}
        WHERE
    """
    stmt += " AND ".join([f"num{k} = %s" for k in range(assignment.num_buckets)])

    crs.execute(stmt, assignment.bucket_content)

    return crs.fetchone()


def add_assignment(
    variant_id: int,
    assignment: game_variant.Assignment,
    autopilot_variant: bool,  # noqa: FBT001
    crs: cursor,
) -> int:
    """
    Add an assignment to the database. Returns the
    ID of the assignment.
    """

    columns = []

    columns.extend([f"num{k}" for k in range(assignment.num_buckets)])
    columns.extend([f"sorted{k}" for k in range(assignment.num_buckets)])
    columns.extend(["autopilot_variant", "metadata"])

    crs.execute(
        f"""
        INSERT INTO {get_variant_tablename(variant_id)}
        ({", ".join(columns)})
        VALUES ({", ".join(["%s"] * len(columns))})
        RETURNING id
        """,
        (
            *assignment.bucket_content,
            *sorted(assignment.bucket_content),
            autopilot_variant,
            "{}",
        ),
    )

    ret = crs.fetchone()

    if ret is None:
        raise RuntimeError("Something went wrong when adding an assignment.")

    return ret[0]


def update_assignment_metadata(
    variant_id: int,
    assignment_id: int,
    data: dict,
    crs: cursor,
) -> None:
    """
    Update the metadata of an assignment, based
    on the given dictionary.
    """

    crs.execute(
        f"""
            SELECT metadata
            FROM {get_variant_tablename(variant_id)}
            WHERE id = %s
        """,
        (assignment_id,),
    )
    data_raw = crs.fetchone()

    if data_raw is None:
        raise RuntimeError("Assignment does not exist!")

    data_db = data_raw[0]
    data_db.update(data)

    crs.execute(
        f"""
        UPDATE {get_variant_tablename(variant_id)}
        SET metadata = %s
        WHERE id = %s
        """,
        (json.dumps(data_db), assignment_id),
    )


def get_assignment_metadata(
    variant_id: int,
    assignment_id: int,
    crs: cursor,
) -> dict | None:
    """
    Returns assignment metadata.
    """

    crs.execute(
        f"""
            SELECT metadata
            FROM {get_variant_tablename(variant_id)}
            WHERE ID = %s
            """,
        (assignment_id,),
    )

    ret = crs.fetchone()

    if ret is None:
        return None

    return ret[0]


def update_assignment_winner(
    variant_id: int,
    assignment_id: int,
    winner: game_variant.KnownWinner,
    crs: cursor,
) -> None:
    """
    Update the metadata of an assignment, based
    on the given dictionary.
    """

    crs.execute(
        f"""
        UPDATE {get_variant_tablename(variant_id)}
        SET winner = %s
        WHERE id = %s
        """,
        (winner.value, assignment_id),
    )


def get_assignment_winner(
    variant_id: int,
    assignment_id: int,
    crs: cursor,
) -> game_variant.KnownWinner | None:
    """
    Returns the winner of an assignment if known.
    """

    crs.execute(
        f"""
        SELECT winner
        FROM {get_variant_tablename(variant_id)}
        WHERE ID = %s
        """,
        (assignment_id,),
    )

    ret = crs.fetchone()

    if ret is None:
        return None

    return ret[0]


def get_assignment_by_id(
    variant_id: int,
    assignment_id: int,
    num_buckets: int,
    crs: cursor,
) -> list[int] | None:
    """
    Get an assignment by it's ID. Returns
    only the bucket contents of the assignment.
    """

    columns = [f"num{k}" for k in range(num_buckets)]

    crs.execute(
        f"""
        SELECT {", ".join(columns)}
        FROM {get_variant_tablename(variant_id)}
        WHERE ID = %s
        """,
        (assignment_id,),
    )

    ret = crs.fetchone()

    if ret is None:
        return None

    return list(ret)


def get_assignment_id_by_bucket_contents(
    variant_id: int,
    bucket_contents: list[int],
    crs: cursor,
) -> int | None:
    """
    Get an assignment ID based on the bucket contents
    """

    number_search = [f"num{k} = %s" for k in range(len(bucket_contents))]

    crs.execute(
        f"""
        SELECT id
        FROM {get_variant_tablename(variant_id)}
        WHERE {" AND ".join(number_search)}
        """,
        bucket_contents,
    )

    ret = crs.fetchone()

    if ret is None:
        return None

    return ret[0]


def find_potential_dominated_autopilot_win(
    variant_id: int,
    bucket_contents: list[int],
    crs: cursor,
) -> Iterator[tuple]:
    """
    Yield assignments that are Autopilot
    variants and that may be dominated
    by the given assignment.
    """

    numbers = [f"num{k}" for k in range(len(bucket_contents))]
    numbers_sorted = [f"sorted{k} <= %s" for k in range(len(bucket_contents))]
    stmt = f"""
    SELECT id, {", ".join(numbers)}
    FROM {get_variant_tablename(variant_id)}
    WHERE autopilot_variant = true
        AND {" AND ".join(numbers_sorted)}
    """

    crs.execute(stmt, sorted(bucket_contents))

    while (ret := crs.fetchone()) is not None:
        yield ret


def assignments_with_unknown_winner(variant_id: int, crs: cursor) -> Iterator[int]:
    """
    Yield assignment IDs that have no winner set
    """

    crs.execute(f"""
        SELECT id
        FROM {get_variant_tablename(variant_id)}
        WHERE winner is null
    """)

    while (ret := crs.fetchone()) is not None:
        yield ret[0]

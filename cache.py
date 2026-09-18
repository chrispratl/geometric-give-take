"""
Logic for interacting with the Cache (Valkey)
"""

import json
import os
from dataclasses import asdict, dataclass
from enum import Enum

from valkey import Valkey

QUEUE_POP_TIMEOUT = 5


def get_connection() -> Valkey:
    return Valkey(
        host=os.environ.get("CACHE_HOST", "cache"),
    )


class JobType(str, Enum):
    AUTOPILOT_ASSIGNMENT = "autopilot_assignment"
    EQUIVALENT_POSITIONS_COMPUTATION = "equivalent_positions_computation"
    DISTANCES_COMPUTER = "distances_computer"
    ASSIGNMENT_COMPUTER = "assignment_computer"
    ASSIGNMENT_GENERATOR = "assignment_generator"
    RECURSIVE_COMPUTATION = "recursive_computation"


@dataclass
class JobOrder:
    """
    Generate a job that the master will send to the workers,
    in order to be processed
    """

    position_id: int
    id: str
    job_type: JobType
    data: dict


class JobProcessStatus(str, Enum):
    CREATED = "created"
    WORKING = "working"
    FINISHED = "finished"

    PREPARED_COMPUTING_JOB = "prepared_computing_job"


@dataclass
class JobProcessStatusPush:
    """
    Generate a job status that the worker
    will send to the manager.
    """

    id: str
    status: JobProcessStatus
    data: dict | None


# Valkey Queue Names
JOB_ORDER_QUEUENAME = "job_order"
JOB_ORDER_LOW_PRIO_QUEUENAME = "job_order_low_prio"
JOB_STATUS_QUEUENAME = "job_status"
INFORMATION_QUEUENAME = "job_information"

###################
# Metrics methods #
###################


def job_order_queuelength(db: Valkey) -> int:

    ret = db.llen(JOB_ORDER_QUEUENAME)

    if not isinstance(ret, int):
        raise TypeError("Received a non-understood value!")

    return ret


###################
# Manager Methods #
###################


def push_job_to_workers(
    job: JobOrder,
    db: Valkey,
    *,
    process_first: bool = False,
    second_queue: bool = False,
) -> None:
    """
    Push a job to the cache to be fetched by the workers.
    There are two processing queues: An important one, and a
    less important one. The workers will first empty the
    first queue, and only process tasks of the second queue
    if the first one is empty.
    * `second_queue`: If the second queue should be used
    * `process_first`: If True, then the job will be added to
      the first queue, independent of the `second_queue` argument.
      Also, it will be added as the first argument to the list.

    So, general rule:
    * `process_first` will be the first tasks to work on
    * Then "normal" tasks are processed
    * `second_queue` task are the ones with least priority
    """

    queue = JOB_ORDER_QUEUENAME
    if not process_first and second_queue:
        queue = JOB_ORDER_LOW_PRIO_QUEUENAME

    method = db.lpush if process_first else db.rpush

    method(queue, json.dumps(asdict(job)))


def pull_job_status(db: Valkey, *, fast: bool = False) -> list[bytes] | None:
    """
    Fetch data from the job status queue.
    """

    timeout = 1 if fast else QUEUE_POP_TIMEOUT

    return db.blpop(
        [JOB_STATUS_QUEUENAME],
        timeout=timeout,
    )  # type: ignore


def fetch_sent_information(db: Valkey) -> list[bytes] | None:
    """
    Fetch data that the workers sent to the manager
    """

    return db.blpop(INFORMATION_QUEUENAME, timeout=QUEUE_POP_TIMEOUT)  # type: ignore


##################
# Worker Methods #
##################


def fetch_job(db: Valkey) -> JobOrder:
    """
    Fetch a job from the orders queues
    The low prio queuename will usually be added
    and also be listened on, though this can be turned off.
    """

    data = None

    queues = [JOB_ORDER_QUEUENAME]
    if os.environ.get("IGNORE_LOW_PRIO_TASKS", "false") != "true":
        queues.append(JOB_ORDER_LOW_PRIO_QUEUENAME)

    while data is None:
        data = db.blpop(
            queues,
            timeout=QUEUE_POP_TIMEOUT,
        )

    return JobOrder(**json.loads(data[1]))  # type: ignore


def push_job_status(job_status: JobProcessStatusPush, db: Valkey) -> None:
    """
    Push a Job Process Status to the manager.
    """

    db.rpush(JOB_STATUS_QUEUENAME, json.dumps(asdict(job_status)))


def send_information_to_manager(data: dict, db: Valkey) -> None:
    """
    Send some information that the manager is interested in.
    """

    db.rpush(INFORMATION_QUEUENAME, json.dumps(data))

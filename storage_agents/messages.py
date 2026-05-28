from dataclasses import dataclass, field
from typing import Any, Optional
import time


TASK_ANNOUNCED = "task.announced"
BID_PROPOSED = "bid.proposed"
TASK_ASSIGNED = "task.assigned"
TASK_ACCEPTED = "task.accepted"
TASK_REJECTED = "task.rejected"
TASK_STARTED = "task.started"
TASK_COMPLETED = "task.completed"
TASK_WAITING = "task.waiting"
TASK_EXPIRED = "task.expired"
TASK_FAILED = "task.failed"
ROBOT_STATUS = "robot.status"
ROBOT_STUCK = "robot.stuck"
ROBOT_PATH_PLANNED = "robot.path_planned"
CELL_REQUESTED = "cell.requested"
CHARGE_REQUESTED = "charge.requested"
CHARGE_GRANTED = "charge.granted"
CHARGE_FINISHED = "charge.finished"


@dataclass(frozen=True)
class Point:
    x: int
    y: int

    @property
    def label(self) -> str:
        return f"{chr(ord('A') + self.x)}{self.y + 1}"

    def distance_to(self, other: "Point") -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def step_towards(self, target: "Point") -> "Point":
        if self.x < target.x:
            return Point(self.x + 1, self.y)
        if self.x > target.x:
            return Point(self.x - 1, self.y)
        if self.y < target.y:
            return Point(self.x, self.y + 1)
        if self.y > target.y:
            return Point(self.x, self.y - 1)
        return self


@dataclass(frozen=True)
class Envelope:
    sender: str
    topic: str
    payload: Any
    recipient: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class WarehouseTask:
    order_id: str
    pickup: Point
    dropoff: Point
    status: str = "bidding"
    assigned_robot: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.order_id}: {self.pickup.label} -> {self.dropoff.label}"


@dataclass(frozen=True)
class Bid:
    order_id: str
    robot_id: str
    eta_seconds: float
    battery_after: float
    score: float
    note: str


@dataclass(frozen=True)
class TaskAssignment:
    task: WarehouseTask
    bid: Bid


@dataclass(frozen=True)
class TaskAccepted:
    order_id: str
    robot_id: str


@dataclass(frozen=True)
class TaskRejected:
    order_id: str
    robot_id: str
    reason: str


@dataclass(frozen=True)
class TaskStarted:
    order_id: str
    robot_id: str


@dataclass(frozen=True)
class TaskCompleted:
    order_id: str
    robot_id: str
    battery_left: float


@dataclass(frozen=True)
class TaskFailed:
    order_id: str
    robot_id: str
    reason: str


@dataclass(frozen=True)
class RobotStatus:
    robot_id: str
    position: Point
    battery: float
    mode: str


@dataclass(frozen=True)
class RobotStuck:
    robot_id: str
    position: Point
    battery: float
    reason: str


@dataclass(frozen=True)
class RobotPathPlanned:
    robot_id: str
    target: Point
    path: tuple[Point, ...]
    mode: str


@dataclass(frozen=True)
class CellRequest:
    robot_id: str
    current: Point
    requested: Point
    mode: str
    priority: float = 0.0


@dataclass(frozen=True)
class ChargeRequest:
    robot_id: str
    position: Point
    battery: float
    reason: str


@dataclass(frozen=True)
class ChargeGrant:
    robot_id: str
    accepted: bool
    station: Optional[Point]
    message: str


@dataclass(frozen=True)
class ChargeFinished:
    robot_id: str
    station: Point

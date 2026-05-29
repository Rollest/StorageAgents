import copy
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from .agents import BaseAgent
from .bus import MessageBus
from .messages import (
    BID_PROPOSED,
    CELL_REQUESTED,
    CHARGE_FINISHED,
    CHARGE_GRANTED,
    CHARGE_REQUESTED,
    ROBOT_PATH_PLANNED,
    ROBOT_STATUS,
    ROBOT_STUCK,
    TASK_ACCEPTED,
    TASK_ANNOUNCED,
    TASK_ASSIGNED,
    TASK_COMPLETED,
    TASK_EXPIRED,
    TASK_FAILED,
    TASK_REJECTED,
    TASK_STARTED,
    TASK_WAITING,
    Bid,
    CellRequest,
    ChargeFinished,
    ChargeGrant,
    ChargeRequest,
    Envelope,
    Point,
    RobotPathPlanned,
    RobotStatus,
    RobotStuck,
    TaskAccepted,
    TaskAssignment,
    TaskCompleted,
    TaskFailed,
    TaskRejected,
    TaskStarted,
    WarehouseTask,
)
from .time_control import SimulationClock
from .world import WarehouseWorld


class WebStateAgent(BaseAgent):
    """Пассивный наблюдатель, превращающий сообщения агентов в снимок для браузера."""

    def __init__(
        self,
        bus: MessageBus,
        world: WarehouseWorld,
        max_events: int = 80,
        clock: Optional[SimulationClock] = None,
    ) -> None:
        """Инициализирует экземпляр."""
        super().__init__("WebStateAgent", bus, observe_all=True, clock=clock)
        self.world = world
        self.started_at = self.clock.elapsed()
        self._lock = threading.RLock()
        self._orders: Dict[str, Dict[str, object]] = {}
        self._robots: Dict[str, Dict[str, object]] = {}
        self._charging_occupied: Dict[str, Point] = {}
        self._charging_waiting: Deque[str] = deque()
        self._events: Deque[Dict[str, object]] = deque(maxlen=max_events)

    async def run(self) -> None:
        """Запускает основной цикл."""
        while self.running and self.inbox is not None:
            message = await self.inbox.get()
            self._apply(message)

    def snapshot(self) -> Dict[str, object]:
        """Возвращает сериализуемый снимок состояния."""
        with self._lock:
            orders = list(self._orders.values())
            active_orders = [
                order
                for order in orders
                if order.get("status")
                in {"waiting", "bidding", "assigned", "accepted", "in_progress"}
            ]
            completed = sum(1 for order in orders if order.get("status") == "completed")
            expired = sum(1 for order in orders if order.get("status") == "expired")
            failed = sum(1 for order in orders if order.get("status") == "failed")
            durations = [
                float(order["completedAt"]) - float(order["startedAt"])
                for order in orders
                if "completedAt" in order and "startedAt" in order
            ]
            busy_robots = sum(
                1
                for robot in self._robots.values()
                if any(
                    token in str(robot.get("mode", "")).lower()
                    for token in ("pickup", "deliver")
                )
            )
            charging_robots = sum(
                1
                for robot in self._robots.values()
                if "charging" in str(robot.get("mode", "")).lower()
            )
            snapshot = {
                "uptimeSeconds": round(self.clock.elapsed() - self.started_at, 1),
                "timeScale": self.clock.snapshot(),
                "world": {
                    "width": self.world.width,
                    "height": self.world.height,
                    "shelves": [self._point(shelf) for shelf in self.world.shelves],
                    "packagingZone": self._point(self.world.packaging_zone),
                    "chargingStations": [
                        self._point(station) for station in self.world.charging_stations
                    ],
                },
                "orders": {
                    "total": len(orders),
                    "completed": completed,
                    "expired": expired,
                    "failed": failed,
                    "avgCompletionSeconds": round(
                        sum(durations) / len(durations), 2
                    )
                    if durations
                    else 0.0,
                    "active": active_orders[-8:],
                },
                "robots": sorted(self._robots.values(), key=lambda item: str(item["id"])),
                "robotStats": {
                    "total": len(self._robots),
                    "busy": busy_robots,
                    "charging": charging_robots,
                    "utilization": round(
                        busy_robots / len(self._robots),
                        2,
                    )
                    if self._robots
                    else 0.0,
                },
                "charging": {
                    "occupied": [
                        {"robotId": robot_id, "station": self._point(station)}
                        for robot_id, station in sorted(self._charging_occupied.items())
                    ],
                    "waiting": list(self._charging_waiting),
                },
                "events": list(self._events)[-18:],
            }
            return copy.deepcopy(snapshot)

    def _apply(self, event: Envelope) -> None:
        """Применяет одно событие шины к веб-состоянию."""
        with self._lock:
            payload = event.payload
            if event.topic == TASK_ANNOUNCED and isinstance(payload, WarehouseTask):
                self._orders[payload.order_id] = self._task_dict(
                    payload,
                    "bidding",
                    created_at=event.created_at,
                )
            elif event.topic == BID_PROPOSED and isinstance(payload, Bid):
                order = self._orders.get(payload.order_id)
                if order is not None:
                    bids = order.setdefault("bids", [])
                    if isinstance(bids, list):
                        bids.append(
                            {
                                "robotId": payload.robot_id,
                                "etaSeconds": round(payload.eta_seconds, 2),
                                "batteryAfter": round(payload.battery_after, 1),
                                "note": payload.note,
                            }
                        )
                        del bids[:-4]
            elif event.topic == TASK_ASSIGNED and isinstance(payload, TaskAssignment):
                previous = self._orders.get(payload.task.order_id, {})
                order = self._task_dict(
                    payload.task,
                    "assigned",
                    assigned_robot=payload.bid.robot_id,
                    created_at=self._event_time(previous, "createdAt", event.created_at),
                )
                order["bids"] = previous.get("bids", [])
                order["reassignments"] = int(previous.get("reassignments", 0)) + (
                    1 if previous.get("assignedRobot") else 0
                )
                self._orders[payload.task.order_id] = order
            elif event.topic == TASK_ACCEPTED and isinstance(payload, TaskAccepted):
                order = self._orders.get(payload.order_id)
                if order is not None:
                    order["status"] = "accepted"
                    order["assignedRobot"] = payload.robot_id
            elif event.topic == TASK_REJECTED and isinstance(payload, TaskRejected):
                order = self._orders.get(payload.order_id)
                if order is not None:
                    order["status"] = "rejected"
                    order["assignedRobot"] = payload.robot_id
                    order["failureReason"] = payload.reason
            elif event.topic == TASK_STARTED and isinstance(payload, TaskStarted):
                order = self._orders.get(payload.order_id)
                if order is not None:
                    order["status"] = "in_progress"
                    order["assignedRobot"] = payload.robot_id
                    order["startedAt"] = event.created_at
            elif event.topic == TASK_COMPLETED and isinstance(payload, TaskCompleted):
                order = self._orders.get(payload.order_id)
                if order is not None:
                    order["status"] = "completed"
                    order["assignedRobot"] = payload.robot_id
                    order["completedAt"] = event.created_at
            elif event.topic == TASK_WAITING and isinstance(payload, WarehouseTask):
                previous = self._orders.get(payload.order_id, {})
                order = self._task_dict(
                    payload,
                    "waiting",
                    created_at=self._event_time(previous, "createdAt", event.created_at),
                )
                order["bids"] = previous.get("bids", [])
                self._orders[payload.order_id] = order
            elif event.topic == TASK_EXPIRED and isinstance(payload, WarehouseTask):
                previous = self._orders.get(payload.order_id, {})
                order = self._task_dict(
                    payload,
                    "expired",
                    created_at=self._event_time(previous, "createdAt", event.created_at),
                )
                order["expiredAt"] = event.created_at
                self._orders[payload.order_id] = order
            elif event.topic == TASK_FAILED and isinstance(payload, TaskFailed):
                order = self._orders.get(payload.order_id)
                if order is not None:
                    order["status"] = "failed"
                    order["assignedRobot"] = payload.robot_id
                    order["failureReason"] = payload.reason
                    order["failedAt"] = event.created_at
            elif event.topic == ROBOT_STATUS and isinstance(payload, RobotStatus):
                previous = self._robots.get(payload.robot_id, {})
                self._robots[payload.robot_id] = {
                    "id": payload.robot_id,
                    "position": self._point(payload.position),
                    "battery": round(payload.battery, 1),
                    "mode": payload.mode,
                    "path": previous.get("path", []),
                    "stuckReason": previous.get("stuckReason")
                    if payload.mode == "stuck"
                    else None,
                }
            elif event.topic == ROBOT_STUCK and isinstance(payload, RobotStuck):
                self._robots[payload.robot_id] = {
                    "id": payload.robot_id,
                    "position": self._point(payload.position),
                    "battery": round(payload.battery, 1),
                    "mode": "stuck",
                    "path": [],
                    "stuckReason": payload.reason,
                }
            elif event.topic == ROBOT_PATH_PLANNED and isinstance(payload, RobotPathPlanned):
                previous = self._robots.get(payload.robot_id, {})
                self._robots[payload.robot_id] = {
                    "id": payload.robot_id,
                    "position": previous.get("position", self._point(payload.target)),
                    "battery": previous.get("battery", 0.0),
                    "mode": previous.get("mode", payload.mode),
                    "path": [self._point(point) for point in payload.path],
                    "stuckReason": previous.get("stuckReason"),
                }
            elif event.topic == CHARGE_REQUESTED and isinstance(payload, ChargeRequest):
                if payload.robot_id not in self._charging_waiting:
                    self._charging_waiting.append(payload.robot_id)
            elif event.topic == CHARGE_GRANTED and isinstance(payload, ChargeGrant):
                if payload.accepted and payload.station is not None:
                    self._drop_waiting(payload.robot_id)
                    self._charging_occupied[payload.robot_id] = payload.station
                elif payload.robot_id not in self._charging_waiting:
                    self._charging_waiting.append(payload.robot_id)
            elif event.topic == CHARGE_FINISHED and isinstance(payload, ChargeFinished):
                self._charging_occupied.pop(payload.robot_id, None)
                self._drop_waiting(payload.robot_id)

            if event.topic not in {ROBOT_STATUS, ROBOT_PATH_PLANNED, CELL_REQUESTED}:
                self._events.append(
                    {
                        "topic": event.topic,
                        "sender": event.sender,
                        "recipient": event.recipient or "all",
                        "text": self._format_event(event),
                        "ageSeconds": round(time.monotonic() - event.created_at, 1),
                    }
                )

    def _drop_waiting(self, robot_id: str) -> None:
        """Удаляет робота из очереди зарядки."""
        self._charging_waiting = deque(
            item for item in self._charging_waiting if item != robot_id
        )

    def _task_dict(
        self,
        task: WarehouseTask,
        status: str,
        assigned_robot: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Dict[str, object]:
        """Создает сериализуемый словарь заказа."""
        data: Dict[str, object] = {
            "id": task.order_id,
            "pickup": self._point(task.pickup),
            "dropoff": self._point(task.dropoff),
            "status": status,
            "assignedRobot": assigned_robot or task.assigned_robot,
            "bids": [],
        }
        if created_at is not None:
            data["createdAt"] = created_at
        return data

    def _event_time(
        self,
        order: Dict[str, object],
        key: str,
        fallback: float,
    ) -> float:
        """Возвращает время события с запасным значением."""
        value = order.get(key, fallback)
        return float(value) if isinstance(value, (float, int)) else fallback

    def _point(self, point: Point) -> Dict[str, object]:
        """Создает сериализуемый словарь точки."""
        return {"x": point.x, "y": point.y, "label": point.label}

    def _format_event(self, event: Envelope) -> str:
        """Форматирует событие."""
        payload = event.payload
        if event.topic == TASK_ANNOUNCED and isinstance(payload, WarehouseTask):
            return f"OrderAgent announced {payload.order_id} at {payload.pickup.label}"
        if event.topic == BID_PROPOSED and isinstance(payload, Bid):
            return (
                f"{payload.robot_id} bid {payload.eta_seconds:.1f}s "
                f"for {payload.order_id}; battery after {payload.battery_after:.1f}%"
            )
        if event.topic == TASK_ASSIGNED and isinstance(payload, TaskAssignment):
            return f"OrderAgent assigned {payload.task.order_id} to {payload.bid.robot_id}"
        if event.topic == TASK_ACCEPTED and isinstance(payload, TaskAccepted):
            return f"{payload.robot_id} accepted {payload.order_id}"
        if event.topic == TASK_REJECTED and isinstance(payload, TaskRejected):
            return f"{payload.robot_id} rejected {payload.order_id}: {payload.reason}"
        if event.topic == TASK_STARTED and isinstance(payload, TaskStarted):
            return f"{payload.robot_id} started {payload.order_id}"
        if event.topic == TASK_WAITING and isinstance(payload, WarehouseTask):
            return f"{payload.order_id} waits for another auction"
        if event.topic == TASK_COMPLETED and isinstance(payload, TaskCompleted):
            return f"{payload.robot_id} completed {payload.order_id}"
        if event.topic == TASK_EXPIRED and isinstance(payload, WarehouseTask):
            return f"{payload.order_id} expired without bids"
        if event.topic == TASK_FAILED and isinstance(payload, TaskFailed):
            return f"{payload.order_id} failed: {payload.robot_id} stuck ({payload.reason})"
        if event.topic == CHARGE_REQUESTED and isinstance(payload, ChargeRequest):
            return f"{payload.robot_id} requested charger: {payload.reason}"
        if event.topic == CHARGE_GRANTED and isinstance(payload, ChargeGrant):
            if payload.accepted and payload.station is not None:
                return f"ChargingStationAgent sent {payload.robot_id} to {payload.station.label}"
            return f"ChargingStationAgent queued {payload.robot_id}"
        if event.topic == CHARGE_FINISHED and isinstance(payload, ChargeFinished):
            return f"{payload.robot_id} finished charging at {payload.station.label}"
        if event.topic == ROBOT_STUCK and isinstance(payload, RobotStuck):
            return f"{payload.robot_id} stuck at {payload.position.label}: {payload.reason}"
        if event.topic == CELL_REQUESTED and isinstance(payload, CellRequest):
            return f"{payload.robot_id} requested {payload.requested.label}"
        return f"{event.sender} -> {event.recipient or 'all'} {event.topic}"

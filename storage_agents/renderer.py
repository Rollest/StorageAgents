import asyncio
import time
from typing import Iterable, List

from .agents import ChargingStationAgent, OrderAgent, RobotAgent
from .bus import MessageBus
from .messages import (
    BID_PROPOSED,
    CELL_REQUESTED,
    CHARGE_FINISHED,
    CHARGE_GRANTED,
    CHARGE_REQUESTED,
    ROBOT_PATH_PLANNED,
    ROBOT_STATUS,
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
    ChargeGrant,
    ChargeRequest,
    Envelope,
    TaskAccepted,
    TaskFailed,
    TaskRejected,
    TaskStarted,
    WarehouseTask,
)
from .world import WarehouseWorld


class ConsoleRenderer:
    """Renders the simulation in the terminal."""
    def __init__(
        self,
        world: WarehouseWorld,
        order_agent: OrderAgent,
        robots: Iterable[RobotAgent],
        charging_agent: ChargingStationAgent,
        bus: MessageBus,
        tick: float = 0.25,
        clear_screen: bool = True,
    ) -> None:
        """Initializes the instance."""
        self.world = world
        self.order_agent = order_agent
        self.robots = list(robots)
        self.charging_agent = charging_agent
        self.bus = bus
        self.tick = tick
        self.clear_screen = clear_screen

    async def run(self, duration: float) -> None:
        """Runs the main loop."""
        start = time.monotonic()
        while time.monotonic() - start < duration:
            self.render(duration - (time.monotonic() - start))
            await asyncio.sleep(self.tick)
        self.render(0.0)

    def render(self, seconds_left: float) -> None:
        """Prints one terminal frame."""
        if self.clear_screen:
            print("\033[H\033[J", end="")
        print("Multi-agent warehouse task allocation")
        print(f"Time left: {max(0.0, seconds_left):4.1f}s")
        print()
        print(self._grid())
        print()
        print(self._orders())
        print()
        print(self._robots())
        print()
        print(self._charging())
        print()
        print("Message log")
        for line in self._message_log():
            print(f"  {line}")

    def _grid(self) -> str:
        """Renders the warehouse grid."""
        grid: List[List[str]] = [
            ["." for _ in range(self.world.width)] for _ in range(self.world.height)
        ]
        for shelf in self.world.shelves:
            grid[shelf.y][shelf.x] = "S"
        for station in self.world.charging_stations:
            grid[station.y][station.x] = "C"
        grid[self.world.packaging_zone.y][self.world.packaging_zone.x] = "P"
        for index, robot in enumerate(self.robots, start=1):
            symbol = str(index)
            if robot.charging:
                symbol = symbol.lower()
            grid[robot.position.y][robot.position.x] = symbol

        header = "   " + " ".join(chr(ord("A") + x) for x in range(self.world.width))
        rows = [header]
        for y, row in enumerate(grid, start=1):
            rows.append(f"{y:2} " + " ".join(row))
        return "\n".join(rows)

    def _orders(self) -> str:
        """Renders order information."""
        active = [
            task
            for task in self.order_agent.orders.values()
            if task.status in {"waiting", "bidding", "assigned", "accepted", "in_progress"}
        ][-5:]
        lines = [
            "Orders: "
            f"done={self.order_agent.completed_count}, "
            f"expired={self.order_agent.expired_count}, "
            f"failed={self.order_agent.failed_count}, "
            f"reassigned={self.order_agent.reassigned_count}, "
            f"avg_delivery={self.order_agent.average_delivery_seconds:.1f}s, "
            f"total={len(self.order_agent.orders)}"
        ]
        if not active:
            lines.append("  no active orders")
            return "\n".join(lines)
        for task in active:
            owner = task.assigned_robot or "auction"
            lines.append(f"  {task.order_id} {task.pickup.label}->P {task.status} by {owner}")
        return "\n".join(lines)

    def _robots(self) -> str:
        """Renders robot information."""
        lines = ["Robots"]
        for robot in self.robots:
            if robot.stuck:
                mode = "stuck"
            elif robot.charging:
                mode = "charging"
            elif robot.busy:
                mode = f"task {robot.current_task_id}"
            elif robot.waiting_for_charge:
                mode = "waiting charger"
            else:
                mode = "idle"
            lines.append(
                f"  {robot.agent_id}: pos={robot.position.label} "
                f"battery={robot.battery:5.1f}% {mode}"
            )
        return "\n".join(lines)

    def _charging(self) -> str:
        """Renders charging information."""
        occupied = ", ".join(
            f"{robot}->{station.label}"
            for robot, station in sorted(self.charging_agent.occupied.items())
        )
        occupied = occupied or "free"
        waiting = ", ".join(request.robot_id for request in self.charging_agent.waiting)
        waiting = waiting or "none"
        return f"Charging stations: {occupied}; queue: {waiting}"

    def _message_log(self) -> List[str]:
        """Returns recent bus messages."""
        visible_events = [
            event
            for event in self.bus.history
            if event.topic not in {ROBOT_STATUS, ROBOT_PATH_PLANNED, CELL_REQUESTED}
        ]
        return [self._format_event(event) for event in visible_events[-12:]]

    def _format_event(self, event: Envelope) -> str:
        """Formats the event."""
        payload = event.payload
        if event.topic == TASK_ANNOUNCED and isinstance(payload, WarehouseTask):
            return f"{event.sender} announced {payload.order_id} at {payload.pickup.label}"
        if event.topic == BID_PROPOSED and isinstance(payload, Bid):
            return (
                f"{payload.robot_id} bid {payload.eta_seconds:.1f}s "
                f"for {payload.order_id}; after={payload.battery_after:.1f}%"
            )
        if event.topic == TASK_ASSIGNED:
            return f"{event.sender} assigned task to {event.recipient}"
        if event.topic == TASK_ACCEPTED and isinstance(payload, TaskAccepted):
            return f"{payload.robot_id} accepted {payload.order_id}"
        if event.topic == TASK_REJECTED and isinstance(payload, TaskRejected):
            return f"{payload.robot_id} rejected {payload.order_id}: {payload.reason}"
        if event.topic == TASK_STARTED and isinstance(payload, TaskStarted):
            return f"{payload.robot_id} started {payload.order_id}"
        if event.topic == TASK_WAITING and isinstance(payload, WarehouseTask):
            return f"{payload.order_id} waits for another auction"
        if event.topic == TASK_COMPLETED:
            return f"{event.sender} completed a task"
        if event.topic == TASK_EXPIRED and isinstance(payload, WarehouseTask):
            return f"{payload.order_id} expired without bids"
        if event.topic == TASK_FAILED and isinstance(payload, TaskFailed):
            return f"{payload.order_id} failed: {payload.robot_id} ({payload.reason})"
        if event.topic == CHARGE_REQUESTED and isinstance(payload, ChargeRequest):
            return f"{payload.robot_id} requested charge: {payload.reason}"
        if event.topic == CHARGE_GRANTED and isinstance(payload, ChargeGrant):
            if payload.accepted and payload.station:
                return f"charger sent {payload.robot_id} to {payload.station.label}"
            return f"charger queued {payload.robot_id}"
        if event.topic == CHARGE_FINISHED:
            return f"{event.sender} finished charging"
        return f"{event.sender} -> {event.recipient or '*'} {event.topic}"

import asyncio
import random
import time
from collections import defaultdict, deque
from dataclasses import replace
from typing import Deque, Dict, List, Optional, Tuple

from .bus import MessageBus
from .learning import (
    CONFLICT_ACTION_SIDE_STEP,
    CONFLICT_ACTION_WAIT,
    ConflictLearningState,
    ConflictQPolicy,
    ConflictStateEncoder,
    point_key,
)
from .metrics import MetricsRecorder, NullMetricsRecorder
from .messages import (
    BID_PROPOSED,
    CELL_REQUESTED,
    CHARGE_FINISHED,
    CHARGE_GRANTED,
    CHARGE_REQUESTED,
    ROBOT_STATUS,
    ROBOT_PATH_PLANNED,
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
from .navigation import plan_path
from .time_control import SimulationClock
from .world import WarehouseWorld


class BaseAgent:
    """Базовый класс для асинхронных складских агентов."""
    def __init__(
        self,
        agent_id: str,
        bus: MessageBus,
        observe_all: bool = False,
        clock: Optional[SimulationClock] = None,
    ) -> None:
        """Инициализирует экземпляр."""
        self.agent_id = agent_id
        self.bus = bus
        self.observe_all = observe_all
        self.clock = clock or SimulationClock()
        self.inbox: Optional[asyncio.Queue] = None
        self.running = False
        self._main_task: Optional[asyncio.Task] = None
        self._children: List[asyncio.Task] = []

    async def start(self) -> None:
        """Запускает задачу агента."""
        self.inbox = await self.bus.subscribe(self.agent_id, observe_all=self.observe_all)
        self.running = True
        self._main_task = asyncio.create_task(self.run(), name=self.agent_id)

    async def stop(self) -> None:
        """Останавливает агента и его дочерние задачи."""
        self.running = False
        tasks = [task for task in [self._main_task, *self._children] if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.bus.unsubscribe(self.agent_id)

    async def run(self) -> None:
        """Запускает основной цикл."""
        raise NotImplementedError

    async def send(self, recipient: str, topic: str, payload: object) -> None:
        """Отправляет сообщение одному получателю."""
        await self.bus.publish(
            Envelope(
                sender=self.agent_id,
                recipient=recipient,
                topic=topic,
                payload=payload,
            )
        )

    async def broadcast(self, topic: str, payload: object) -> None:
        """Рассылает сообщение всем подписчикам."""
        await self.bus.publish(
            Envelope(sender=self.agent_id, topic=topic, payload=payload)
        )

    async def sleep(self, seconds: float) -> None:
        """Ожидает в симуляционном времени."""
        await self.clock.sleep(seconds)


class OrderAgent(BaseAgent):
    """Создает заказы и запускает небольшой аукцион для каждого."""

    def __init__(
        self,
        bus: MessageBus,
        world: WarehouseWorld,
        order_interval: float = 2.4,
        bid_window: float = 0.7,
        max_orders: int = 10,
        seed: int = 7,
        max_auction_retries: int = 3,
        clock: Optional[SimulationClock] = None,
    ) -> None:
        """Инициализирует экземпляр."""
        super().__init__("OrderAgent", bus, clock=clock)
        self.world = world
        self.order_interval = order_interval
        self.bid_window = bid_window
        self.max_orders = max_orders
        self.max_auction_retries = max_auction_retries
        self.random = random.Random(seed)
        self.orders: Dict[str, WarehouseTask] = {}
        self.pending_bids: Dict[str, List[Bid]] = defaultdict(list)
        self.auction_attempts: Dict[str, int] = defaultdict(int)
        self.created_at: Dict[str, float] = {}
        self.started_at: Dict[str, float] = {}
        self.delivery_durations: List[float] = []
        self.completed_count = 0
        self.expired_count = 0
        self.failed_count = 0
        self.reassigned_count = 0

    async def run(self) -> None:
        """Запускает основной цикл."""
        producer = asyncio.create_task(self._produce_orders())
        self._children.append(producer)

        while self.running and self.inbox is not None:
            message = await self.inbox.get()
            if message.topic == BID_PROPOSED:
                bid = message.payload
                task = self.orders.get(bid.order_id)
                if task and task.status == "bidding":
                    self.pending_bids[bid.order_id].append(bid)
            elif message.topic == TASK_ACCEPTED:
                await self._handle_task_accepted(message.payload)
            elif message.topic == TASK_REJECTED:
                await self._handle_task_rejected(message.payload)
            elif message.topic == TASK_STARTED:
                await self._handle_task_started(message.payload)
            elif message.topic == TASK_COMPLETED:
                report = message.payload
                if isinstance(report, TaskCompleted):
                    task = self.orders.get(report.order_id)
                    if task and task.status != "completed":
                        self.orders[report.order_id] = replace(
                            task,
                            status="completed",
                            assigned_robot=report.robot_id,
                        )
                        started_at = self.started_at.get(report.order_id)
                        if started_at is not None:
                            self.delivery_durations.append(time.monotonic() - started_at)
                        self.completed_count += 1
            elif message.topic == TASK_FAILED:
                report = message.payload
                if isinstance(report, TaskFailed):
                    task = self.orders.get(report.order_id)
                    if task and task.status != "failed":
                        self.orders[report.order_id] = replace(
                            task,
                            status="failed",
                            assigned_robot=report.robot_id,
                        )
                        self.failed_count += 1
            elif message.topic == ROBOT_STUCK:
                await self._handle_robot_stuck(message.payload)

    @property
    def average_delivery_seconds(self) -> float:
        """Возвращает среднюю длительность задачи."""
        if not self.delivery_durations:
            return 0.0
        return sum(self.delivery_durations) / len(self.delivery_durations)

    async def _produce_orders(self) -> None:
        """Создает заказы до достижения заданного лимита."""
        for number in range(1, self.max_orders + 1):
            if not self.running:
                break
            pickup = self.random.choice(self.world.shelves)
            task = WarehouseTask(
                order_id=f"O{number:03d}",
                pickup=pickup,
                dropoff=self.world.packaging_zone,
            )
            self.orders[task.order_id] = task
            self.created_at[task.order_id] = time.monotonic()
            await self.broadcast(TASK_ANNOUNCED, task)
            await self.sleep(self.bid_window)
            await self._assign_best_bid(task.order_id)
            await self.sleep(self.order_interval)

    async def _assign_best_bid(self, order_id: str) -> None:
        """Назначает заказ лучшей доступной ставке."""
        task = self.orders[order_id]
        bids = self.pending_bids.get(order_id, [])
        if not bids:
            if task.status in {"bidding", "assigned", "accepted", "in_progress"}:
                await self._wait_or_expire_order(order_id, task)
            return

        bid = min(bids, key=lambda item: (item.score, item.eta_seconds, -item.battery_after))
        self.pending_bids[order_id] = [
            item for item in bids if item.robot_id != bid.robot_id
        ]
        if task.assigned_robot and task.assigned_robot != bid.robot_id:
            self.reassigned_count += 1
        assigned_task = replace(task, status="assigned", assigned_robot=bid.robot_id)
        self.orders[order_id] = assigned_task
        await self.send(
            bid.robot_id,
            TASK_ASSIGNED,
            TaskAssignment(task=assigned_task, bid=bid),
        )

    async def _wait_or_expire_order(self, order_id: str, task: WarehouseTask) -> None:
        """Повторяет заказ или помечает его истекшим."""
        self.auction_attempts[order_id] += 1
        if self.auction_attempts[order_id] <= self.max_auction_retries:
            waiting_task = replace(task, status="waiting", assigned_robot=None)
            self.orders[order_id] = waiting_task
            await self.broadcast(TASK_WAITING, waiting_task)
            retry = asyncio.create_task(
                self._retry_order_after_delay(order_id),
                name=f"retry-{order_id}",
            )
            self._children.append(retry)
            return

        expired_task = replace(task, status="expired", assigned_robot=None)
        self.orders[order_id] = expired_task
        self.expired_count += 1
        await self.broadcast(TASK_EXPIRED, expired_task)

    async def _retry_order_after_delay(self, order_id: str) -> None:
        """Переоткрывает заказ после задержки повтора."""
        await self.sleep(self.order_interval)
        if not self.running:
            return
        task = self.orders.get(order_id)
        if not task or task.status != "waiting":
            return
        retry_task = replace(task, status="bidding", assigned_robot=None)
        self.orders[order_id] = retry_task
        self.pending_bids.pop(order_id, None)
        await self.broadcast(TASK_ANNOUNCED, retry_task)
        await self.sleep(self.bid_window)
        if self.running:
            await self._assign_best_bid(order_id)

    async def _handle_task_accepted(self, report: object) -> None:
        """Обрабатывает сообщение принятия задачи."""
        if not isinstance(report, TaskAccepted):
            return
        task = self.orders.get(report.order_id)
        if (
            task
            and task.status == "assigned"
            and task.assigned_robot == report.robot_id
        ):
            self.orders[report.order_id] = replace(task, status="accepted")

    async def _handle_task_rejected(self, report: object) -> None:
        """Обрабатывает сообщение отклонения задачи."""
        if not isinstance(report, TaskRejected):
            return
        task = self.orders.get(report.order_id)
        if (
            not task
            or task.status not in {"assigned", "accepted", "in_progress"}
            or task.assigned_robot != report.robot_id
        ):
            return
        self.pending_bids[report.order_id] = [
            bid
            for bid in self.pending_bids.get(report.order_id, [])
            if bid.robot_id != report.robot_id
        ]
        await self._assign_best_bid(report.order_id)

    async def _handle_task_started(self, report: object) -> None:
        """Обрабатывает сообщение начала задачи."""
        if not isinstance(report, TaskStarted):
            return
        task = self.orders.get(report.order_id)
        if (
            task
            and task.status in {"assigned", "accepted"}
            and task.assigned_robot == report.robot_id
        ):
            self.orders[report.order_id] = replace(task, status="in_progress")
            self.started_at[report.order_id] = time.monotonic()

    async def _handle_robot_stuck(self, report: RobotStuck) -> None:
        """Обрабатывает сообщение застревания робота."""
        if not isinstance(report, RobotStuck):
            return
        for task in self.orders.values():
            if (
                task.assigned_robot == report.robot_id
                and task.status in {"assigned", "accepted", "in_progress"}
            ):
                self.orders[task.order_id] = replace(task, status="failed")
                self.failed_count += 1
                await self.broadcast(
                    TASK_FAILED,
                    TaskFailed(
                        order_id=task.order_id,
                        robot_id=report.robot_id,
                        reason=report.reason,
                    ),
                )


class RobotAgent(BaseAgent):
    """Двигается по складу и выполняет назначенные заказы."""
    def __init__(
        self,
        agent_id: str,
        bus: MessageBus,
        world: WarehouseWorld,
        position: Point,
        battery: float = 100.0,
        step_delay: float = 0.25,
        conflict_policy: Optional[ConflictQPolicy] = None,
        metrics: Optional[MetricsRecorder] = None,
        clock: Optional[SimulationClock] = None,
    ) -> None:
        """Инициализирует экземпляр."""
        super().__init__(agent_id, bus, clock=clock)
        self.world = world
        self.position = position
        self.battery = battery
        self.max_battery = 100.0
        self.low_battery_threshold = 25.0
        self.energy_per_step = 1.0
        self.reserve_steps = 10
        self.bid_energy_margin = 5.0
        self.traffic_energy_margin = 8.0
        self.minimum_workload_coverage = 0.35
        self.charge_per_tick = 8.0
        self.step_delay = step_delay
        self.busy = False
        self.charging = False
        self.waiting_for_charge = False
        self.stuck = False
        self.current_task_id: Optional[str] = None
        self.peer_positions: Dict[str, Point] = {}
        self.peer_intents: Dict[str, Tuple[Point, Point, float, float]] = {}
        self.blocked_attempts: Dict[Point, int] = defaultdict(int)
        self.conflict_counts: Dict[Tuple[str, Point], int] = defaultdict(int)
        self.yielding_to: Optional[str] = None
        self.yield_cell: Optional[Point] = None
        self.yield_until = 0.0
        self.side_step_threshold = 4
        self.conflict_policy = conflict_policy or ConflictQPolicy(enabled=False)
        self.conflict_encoder = ConflictStateEncoder()
        self.metrics = metrics or NullMetricsRecorder()
        self.last_conflict_state: Optional[ConflictLearningState] = None
        self.last_conflict_action: Optional[str] = None
        self.last_conflict_cell: Optional[Point] = None
        self.last_conflict_attempts = 0
        self.last_conflict_battery = 100.0
        self.random = random.Random(sum(ord(character) for character in agent_id))
        self.intent_window = min(0.08, max(0.02, self.step_delay / 2))

    async def stop(self) -> None:
        """Останавливает агента и его дочерние задачи."""
        if self.last_conflict_state is not None:
            self._finish_conflict_learning("interrupted")
        if self.conflict_policy.enabled:
            self.conflict_policy.save()
        await super().stop()

    async def run(self) -> None:
        """Запускает основной цикл."""
        await self.publish_status("idle")
        if self.needs_charge_for_workload():
            await self.request_charge("initial reserve is below safe workload estimate")

        while self.running and self.inbox is not None:
            message = await self.inbox.get()
            if message.topic == TASK_ANNOUNCED:
                await self._handle_task_announcement(message.payload)
            elif message.topic == TASK_ASSIGNED:
                await self._handle_task_assignment(message.payload)
            elif message.topic == CHARGE_GRANTED:
                await self._handle_charge_grant(message.payload)
            elif message.topic == ROBOT_STATUS:
                self._handle_peer_status(message.payload)
            elif message.topic == CELL_REQUESTED:
                self._handle_cell_request(message.payload, message.created_at)
            elif message.topic == ROBOT_STUCK:
                self._handle_peer_stuck(message.payload)

    async def _handle_task_announcement(self, task: WarehouseTask) -> None:
        """Обрабатывает сообщение объявления задачи."""
        bid = self.make_bid_for(task)
        if bid is None:
            if self.needs_charge_for_workload() and not self.waiting_for_charge:
                await self.request_charge("not enough reserve for safe next task")
            return
        await self.send("OrderAgent", BID_PROPOSED, bid)

    async def _handle_task_assignment(self, assignment: TaskAssignment) -> None:
        """Обрабатывает сообщение назначения задачи."""
        if not isinstance(assignment, TaskAssignment):
            return
        if assignment.task.assigned_robot not in {None, self.agent_id}:
            return
        if self.busy or self.charging or self.waiting_for_charge or self.stuck:
            await self._reject_task_assignment(assignment, "robot is not available")
            return
        if self.make_bid_for(assignment.task) is None:
            await self._reject_task_assignment(
                assignment,
                "route or battery reserve is no longer sufficient",
            )
            return
        self.busy = True
        self.current_task_id = assignment.task.order_id
        await self.send(
            "OrderAgent",
            TASK_ACCEPTED,
            TaskAccepted(
                order_id=assignment.task.order_id,
                robot_id=self.agent_id,
            ),
        )
        task = asyncio.create_task(self._execute_task(assignment.task))
        self._children.append(task)

    async def _reject_task_assignment(
        self,
        assignment: TaskAssignment,
        reason: str,
    ) -> None:
        """Отклоняет назначение с указанием причины."""
        await self.send(
            "OrderAgent",
            TASK_REJECTED,
            TaskRejected(
                order_id=assignment.task.order_id,
                robot_id=self.agent_id,
                reason=reason,
            ),
        )

    async def _handle_charge_grant(self, grant: ChargeGrant) -> None:
        """Обрабатывает сообщение выдачи зарядки."""
        if grant.robot_id != self.agent_id:
            return
        if not grant.accepted:
            self.waiting_for_charge = True
            return
        if self.busy or self.charging or self.stuck or grant.station is None:
            return
        if not self.can_reach(grant.station):
            self.waiting_for_charge = False
            await self._become_stuck(f"cannot reach charger at {grant.station.label}")
            return
        self.waiting_for_charge = False
        self.charging = True
        task = asyncio.create_task(self._go_charge(grant.station))
        self._children.append(task)

    def make_bid_for(self, task: WarehouseTask) -> Optional[Bid]:
        """Формирует ставку, если робот может безопасно взять задачу."""
        if self.busy or self.charging or self.waiting_for_charge or self.stuck:
            return None

        route = self._task_route(task)
        if route is None:
            return None
        work_distance = len(route)
        energy_needed = self._energy_needed_for_task(task)
        if energy_needed is None:
            return None
        battery_after = self.battery - work_distance * self.energy_per_step

        if self.battery < energy_needed:
            return None

        safe_after = battery_after - self._energy_to_nearest_charger(task.dropoff)
        low_battery_penalty = 10.0 if safe_after < self.reserve_energy else 0.0
        eta_seconds = work_distance * self.step_delay
        score = eta_seconds + low_battery_penalty
        note = "low battery after task" if low_battery_penalty else "ready"

        return Bid(
            order_id=task.order_id,
            robot_id=self.agent_id,
            eta_seconds=eta_seconds,
            battery_after=battery_after,
            score=score,
            note=note,
        )

    async def _execute_task(self, task: WarehouseTask) -> None:
        """Выполняет подбор и доставку для задачи."""
        await self.broadcast(
            TASK_STARTED,
            TaskStarted(order_id=task.order_id, robot_id=self.agent_id),
        )
        if not await self._move_to_shelf_access(
            task.pickup,
            mode=f"pickup {task.order_id}",
            task=task,
        ):
            await self._abort_task_for_charge(
                task,
                "battery reserve would be unsafe before pickup",
            )
            return
        await self.sleep(self.step_delay)
        if not self._has_delivery_energy(task.dropoff):
            await self._abort_task_for_charge(task, "battery reserve too low after pickup")
            return
        if not await self._move_to(
            task.dropoff,
            mode=f"deliver {task.order_id}",
            task=task,
            after_pickup=True,
        ):
            await self._abort_task_for_charge(
                task,
                "battery reserve would be unsafe before delivery",
            )
            return
        await self.send(
            "OrderAgent",
            TASK_COMPLETED,
            TaskCompleted(
                order_id=task.order_id,
                robot_id=self.agent_id,
                battery_left=self.battery,
            ),
        )
        self.busy = False
        self.current_task_id = None
        await self.publish_status("idle")
        if self.needs_charge_for_workload():
            await self.request_charge("not enough reserve for another safe task")

    async def request_charge(self, reason: str) -> None:
        """Запрашивает зарядку, когда роботу нужна энергия."""
        if self.busy or self.charging or self.waiting_for_charge or self.stuck:
            return
        if not any(self.can_reach(station) for station in self.world.charging_stations):
            await self._become_stuck("cannot reach any charger")
            return
        self.waiting_for_charge = True
        await self.send(
            "ChargingStationAgent",
            CHARGE_REQUESTED,
            ChargeRequest(
                robot_id=self.agent_id,
                position=self.position,
                battery=self.battery,
                reason=reason,
            ),
        )

    async def _go_charge(self, station: Point) -> None:
        """Едет к зарядке и пополняет батарею."""
        if not await self._move_to(station, mode="to charger"):
            self.charging = False
            return
        while self.running and self.battery < self.max_battery:
            self.battery = min(self.max_battery, self.battery + self.charge_per_tick)
            await self.publish_status("charging")
            await self.sleep(self.step_delay)
        self.charging = False
        await self.send(
            "ChargingStationAgent",
            CHARGE_FINISHED,
            ChargeFinished(robot_id=self.agent_id, station=station),
        )
        await self.publish_status("idle")

    def can_reach(self, target: Point) -> bool:
        """Проверяет, хватит ли роботу энергии до цели."""
        path = self._path_to(self.position, target, avoid_peers=False)
        if path is None:
            return False
        return self.battery >= len(path) * self.energy_per_step

    @property
    def reserve_energy(self) -> float:
        """Возвращает резерв энергии батареи."""
        return self.reserve_steps * self.energy_per_step

    def _energy_to_nearest_charger(self, start: Point) -> float:
        """Возвращает энергию до ближайшей зарядки."""
        paths = [
            path
            for station in self.world.charging_stations
            for path in [self._path_to(start, station, avoid_peers=False)]
            if path is not None
        ]
        if not paths:
            return float("inf")
        return min(len(path) for path in paths) * self.energy_per_step

    def _energy_needed_for_task(
        self,
        task: WarehouseTask,
        start: Optional[Point] = None,
        include_traffic_margin: bool = True,
    ) -> Optional[float]:
        """Возвращает энергию, нужную для задачи."""
        route = self._task_route(task, start=start)
        if route is None:
            return None
        charger_energy = self._energy_to_nearest_charger(task.dropoff)
        if charger_energy == float("inf"):
            return None
        traffic_margin = self.traffic_energy_margin if include_traffic_margin else 0.0
        return (
            len(route) * self.energy_per_step
            + charger_energy
            + self.reserve_energy
            + self.bid_energy_margin
            + traffic_margin
        )

    def _has_safe_energy_after_step(
        self,
        task: WarehouseTask,
        next_step: Point,
        after_pickup: bool = False,
    ) -> bool:
        """Проверяет, останется ли безопасный запас после шага."""
        battery_after_step = self.battery - self.energy_per_step
        if after_pickup:
            needed = self._energy_needed_for_delivery_from(task.dropoff, next_step)
        else:
            needed = self._energy_needed_for_task(task, start=next_step)
        if needed is None:
            return False
        return battery_after_step >= needed

    def _energy_needed_for_delivery_from(
        self,
        dropoff: Point,
        start: Point,
        include_traffic_margin: bool = True,
    ) -> Optional[float]:
        """Возвращает энергию, нужную для доставки из точки."""
        to_dropoff = self._path_to(start, dropoff, avoid_peers=False)
        if to_dropoff is None:
            return None
        charger_energy = self._energy_to_nearest_charger(dropoff)
        if charger_energy == float("inf"):
            return None
        traffic_margin = self.traffic_energy_margin if include_traffic_margin else 0.0
        return (
            len(to_dropoff) * self.energy_per_step
            + charger_energy
            + self.reserve_energy
            + traffic_margin
        )

    async def _abort_task_for_charge(self, task: WarehouseTask, reason: str) -> None:
        """Прерывает задачу и запрашивает зарядку."""
        self.busy = False
        self.current_task_id = None
        await self.send(
            "OrderAgent",
            TASK_REJECTED,
            TaskRejected(
                order_id=task.order_id,
                robot_id=self.agent_id,
                reason=reason,
            ),
        )
        await self.publish_status("seeking charger")
        await self.request_charge(reason)

    def minimum_safe_task_energy(self) -> Optional[float]:
        """Возвращает минимальную безопасную оценку энергии задачи."""
        energies = self._workload_energy_estimates()
        return min(energies) if energies else None

    def safe_workload_coverage(self) -> float:
        """Возвращает долю задач, безопасных для текущего заряда."""
        energies = self._workload_energy_estimates()
        if not energies:
            return 0.0
        safe_count = sum(1 for energy in energies if self.battery >= energy)
        return safe_count / len(energies)

    def needs_charge_for_workload(self) -> bool:
        """Проверяет, слишком ли мал заряд для работы."""
        if self.busy or self.charging or self.waiting_for_charge or self.stuck:
            return False
        if not any(self.can_reach(station) for station in self.world.charging_stations):
            return True
        charger_reserve = self._energy_to_nearest_charger(self.position) + self.reserve_energy
        if self.battery < charger_reserve:
            return True
        minimum_task_energy = self.minimum_safe_task_energy()
        if minimum_task_energy is not None and self.battery < minimum_task_energy:
            return True
        return self.safe_workload_coverage() < self.minimum_workload_coverage

    def _workload_energy_estimates(self) -> List[float]:
        """Возвращает безопасные оценки энергии для всех стеллажей."""
        return [
            energy
            for shelf in self.world.shelves
            for energy in [
                self._energy_needed_for_task(
                    WarehouseTask(
                        order_id="probe",
                        pickup=shelf,
                        dropoff=self.world.packaging_zone,
                    )
                )
            ]
            if energy is not None
        ]

    async def _move_to(
        self,
        target: Point,
        mode: str,
        task: Optional[WarehouseTask] = None,
        after_pickup: bool = False,
    ) -> bool:
        """Ведет робота к цели."""
        while self.running and self.position != target:
            path = self._path_to(self.position, target)
            if not path:
                static_path = self._path_to(self.position, target, avoid_peers=False)
                if static_path is None:
                    await self._become_stuck(f"no route to {target.label}")
                    return False
                await self.broadcast(
                    ROBOT_PATH_PLANNED,
                    RobotPathPlanned(
                        robot_id=self.agent_id,
                        target=target,
                        path=tuple(static_path),
                        mode=f"waiting for route to {target.label}",
                    ),
                )
                await self.sleep(self.step_delay)
                continue
            if len(path) * self.energy_per_step > self.battery:
                static_path = self._path_to(self.position, target, avoid_peers=False)
                if static_path is not None and len(static_path) * self.energy_per_step <= self.battery:
                    await self.broadcast(
                        ROBOT_PATH_PLANNED,
                        RobotPathPlanned(
                            robot_id=self.agent_id,
                            target=target,
                            path=tuple(static_path),
                            mode=f"waiting for shorter route to {target.label}",
                        ),
                    )
                    await self.sleep(self.step_delay)
                    continue
                await self._become_stuck(f"not enough battery to reach {target.label}")
                return False
            await self.broadcast(
                ROBOT_PATH_PLANNED,
                RobotPathPlanned(
                    robot_id=self.agent_id,
                    target=target,
                    path=tuple(path),
                    mode=mode,
                ),
            )
            next_step = path[0]
            if task is not None and not self._has_safe_energy_after_step(
                task,
                next_step,
                after_pickup=after_pickup,
            ):
                return False
            if not await self._claim_next_cell(next_step, mode):
                action = self._choose_conflict_action(next_step, mode)
                if action == CONFLICT_ACTION_SIDE_STEP:
                    if await self._try_side_step(
                        next_step,
                        mode,
                        task=task,
                        after_pickup=after_pickup,
                    ):
                        self._finish_conflict_learning("side_step_resolved")
                        continue
                    self._finish_conflict_learning("side_step_failed")
                await self.sleep(self.step_delay + self._blocked_backoff(next_step))
                continue
            if self.battery < self.energy_per_step:
                await self._become_stuck(f"battery empty while trying to reach {target.label}")
                return False
            self.position = next_step
            self.battery = max(0.0, self.battery - self.energy_per_step)
            await self.publish_status(mode)
            await self.sleep(self.step_delay)
        await self.broadcast(
            ROBOT_PATH_PLANNED,
            RobotPathPlanned(robot_id=self.agent_id, target=target, path=tuple(), mode=mode),
        )
        self._finish_conflict_learning("route_complete")
        return True

    async def _move_to_shelf_access(
        self,
        shelf: Point,
        mode: str,
        task: Optional[WarehouseTask] = None,
    ) -> bool:
        """Ведет робота к точке доступа стеллажа."""
        access_points = self.world.access_points_for(shelf)
        if not access_points:
            await self._become_stuck(f"no access point for shelf {shelf.label}")
            return False

        while self.running and self.position not in access_points:
            target, path = self._best_path_to_any(access_points, avoid_peers=True)
            if target is None or path is None:
                static_target, static_path = self._best_path_to_any(
                    access_points,
                    avoid_peers=False,
                )
                if static_target is None or static_path is None:
                    await self._become_stuck(f"no route to shelf {shelf.label}")
                    return False
                await self.broadcast(
                    ROBOT_PATH_PLANNED,
                    RobotPathPlanned(
                        robot_id=self.agent_id,
                        target=static_target,
                        path=tuple(static_path),
                        mode=f"waiting for access to {shelf.label}",
                    ),
                )
                await self.sleep(self.step_delay)
                continue

            if len(path) * self.energy_per_step > self.battery:
                await self._become_stuck(f"not enough battery to reach shelf {shelf.label}")
                return False

            await self.broadcast(
                ROBOT_PATH_PLANNED,
                RobotPathPlanned(
                    robot_id=self.agent_id,
                    target=target,
                    path=tuple(path),
                    mode=mode,
                ),
            )
            next_step = path[0]
            if task is not None and not self._has_safe_energy_after_step(task, next_step):
                return False
            if not await self._claim_next_cell(next_step, mode):
                action = self._choose_conflict_action(next_step, mode)
                if action == CONFLICT_ACTION_SIDE_STEP:
                    if await self._try_side_step(next_step, mode, task=task):
                        self._finish_conflict_learning("side_step_resolved")
                        continue
                    self._finish_conflict_learning("side_step_failed")
                await self.sleep(self.step_delay + self._blocked_backoff(next_step))
                continue
            if self.battery < self.energy_per_step:
                await self._become_stuck(f"battery empty while trying to reach {shelf.label}")
                return False
            self.position = next_step
            self.battery = max(0.0, self.battery - self.energy_per_step)
            await self.publish_status(mode)
            await self.sleep(self.step_delay)

        await self.broadcast(
            ROBOT_PATH_PLANNED,
            RobotPathPlanned(
                robot_id=self.agent_id,
                target=self.position,
                path=tuple(),
                mode=mode,
            ),
        )
        self._finish_conflict_learning("route_complete")
        return True

    async def _claim_next_cell(self, next_step: Point, mode: str) -> bool:
        """Занимает следующую клетку, если это позволяют приоритеты."""
        if self._is_yielding_for(next_step):
            self._record_move_blocked(next_step, self.yielding_to)
            return False

        priority = self._movement_priority(next_step, mode)
        await self.broadcast(
            CELL_REQUESTED,
            CellRequest(
                robot_id=self.agent_id,
                current=self.position,
                requested=next_step,
                mode=mode,
                priority=priority,
            ),
        )
        await self.sleep(self.intent_window)
        self._prune_peer_intents()
        if self.world.is_service_cell(next_step):
            self._record_move_success(next_step)
            return True
        for robot_id, position in self.peer_positions.items():
            if position == next_step:
                self._commit_yield(robot_id, next_step)
                self._record_move_blocked(next_step, robot_id)
                return False
        for robot_id, (current, requested, _, peer_priority) in self.peer_intents.items():
            committed_yield = self._is_yielding_to(robot_id, next_step)
            head_on_swap = current == next_step and requested == self.position
            peer_wins = peer_priority > priority or (
                peer_priority == priority and robot_id < self.agent_id
            )
            if (requested == next_step or head_on_swap) and (peer_wins or committed_yield):
                self._commit_yield(robot_id, next_step)
                self._record_move_blocked(next_step, robot_id)
                return False
        self._record_move_success(next_step)
        return True

    def _movement_priority(self, next_step: Point, mode: str) -> float:
        """Возвращает приоритет движения для запрошенной клетки."""
        mode_lower = mode.lower()
        pressure = min(self.blocked_attempts[next_step], 10) * 0.5
        low_battery_bonus = max(0.0, self.low_battery_threshold - self.battery) / 10.0
        charge_bonus = 5.0 if "charger" in mode_lower or self.charging else 0.0
        delivery_bonus = 3.0 if "deliver" in mode_lower else 0.0
        pickup_bonus = 2.0 if "pickup" in mode_lower else 0.0
        busy_bonus = 0.5 if self.busy else 0.0
        return round(
            pressure
            + low_battery_bonus
            + charge_bonus
            + delivery_bonus
            + pickup_bonus
            + busy_bonus,
            3,
        )

    def _record_move_blocked(
        self,
        point: Point,
        robot_id: Optional[str] = None,
    ) -> None:
        """Записывает заблокированную попытку движения."""
        self.blocked_attempts[point] += 1
        if robot_id is not None:
            self.conflict_counts[(robot_id, point)] += 1

    def _record_move_success(self, point: Point) -> None:
        """Записывает успешную попытку движения."""
        self.blocked_attempts.pop(point, None)
        self._clear_expired_yield()
        if self.last_conflict_cell == point:
            self._finish_conflict_learning("resolved")

    def _blocked_backoff(self, point: Point) -> float:
        """Возвращает дополнительное ожидание после повторной блокировки."""
        attempts = self.blocked_attempts.get(point, 0)
        deterministic = min(self.step_delay * attempts * 0.25, self.step_delay * 2)
        jitter = self.random.uniform(0.0, self.intent_window)
        return deterministic + jitter

    def _choose_conflict_action(self, blocked_cell: Point, mode: str) -> str:
        """Выбирает действие в конфликте."""
        if self.last_conflict_state is not None:
            outcome = (
                "repeated"
                if self.last_conflict_cell == blocked_cell
                else "rerouted"
            )
            self._finish_conflict_learning(outcome)
        state = self._encode_conflict_state(blocked_cell, mode)
        side_step = self._choose_side_step(blocked_cell)
        allowed = (
            (CONFLICT_ACTION_WAIT, CONFLICT_ACTION_SIDE_STEP)
            if side_step is not None and self._should_side_step(blocked_cell)
            else (CONFLICT_ACTION_WAIT,)
        )
        action = self.conflict_policy.choose_action(state, allowed_actions=allowed)
        self.last_conflict_state = state
        self.last_conflict_action = action
        self.last_conflict_cell = blocked_cell
        self.last_conflict_attempts = self.blocked_attempts.get(blocked_cell, 0)
        self.last_conflict_battery = self.battery
        self.metrics.record(
            "conflict_action",
            robot=self.agent_id,
            cell=point_key(blocked_cell),
            action=action,
            state=state.key(),
            battery=round(self.battery, 1),
        )
        return action

    def _finish_conflict_learning(self, outcome: str) -> None:
        """Применяет итоговую награду за действие в конфликте."""
        if self.last_conflict_state is None or self.last_conflict_action is None:
            return
        reward = self._conflict_reward(outcome)
        self.conflict_policy.update(
            self.last_conflict_state,
            self.last_conflict_action,
            reward,
        )
        self.metrics.record(
            "conflict_reward",
            robot=self.agent_id,
            cell=point_key(self.last_conflict_cell)
            if self.last_conflict_cell is not None
            else None,
            action=self.last_conflict_action,
            reward=reward,
            outcome=outcome,
            attempts=self.last_conflict_attempts,
            battery=round(self.last_conflict_battery, 1),
        )
        self.last_conflict_state = None
        self.last_conflict_action = None
        self.last_conflict_cell = None
        self.last_conflict_attempts = 0
        self.last_conflict_battery = self.battery

    def _conflict_reward(self, outcome: str) -> float:
        """Вычисляет награду за исход конфликта."""
        attempts = max(self.last_conflict_attempts, 1)
        action = self.last_conflict_action or CONFLICT_ACTION_WAIT
        action_cost = -0.15 if action == CONFLICT_ACTION_WAIT else -0.45
        repeated_cost = min(attempts * 0.18, 1.8)
        battery_cost = 0.0
        if self.last_conflict_battery < 15:
            battery_cost = -3.0
        elif self.last_conflict_battery < self.low_battery_threshold:
            battery_cost = -1.2

        if outcome in {"resolved", "route_complete", "side_step_resolved"}:
            reward = 3.0 + action_cost - repeated_cost * 0.35 + battery_cost
            if action == CONFLICT_ACTION_SIDE_STEP:
                reward += 0.8 if attempts >= self.side_step_threshold else -0.35
            return round(reward, 3)

        if outcome == "repeated":
            return round(-0.8 + action_cost - repeated_cost + battery_cost, 3)
        if outcome == "side_step_failed":
            return round(-2.2 + action_cost - repeated_cost + battery_cost, 3)
        if outcome == "rerouted":
            return round(-0.35 + action_cost - repeated_cost * 0.5 + battery_cost, 3)
        return round(action_cost - repeated_cost + battery_cost, 3)

    def _encode_conflict_state(
        self,
        blocked_cell: Point,
        mode: str,
    ) -> ConflictLearningState:
        """Кодирует текущий конфликт для обучения."""
        own_priority = self._movement_priority(blocked_cell, mode)
        peer_priority = max(
            (
                priority
                for _, requested, _, priority in self.peer_intents.values()
                if requested == blocked_cell
            ),
            default=0.0,
        )
        return self.conflict_encoder.encode(
            battery=self.battery,
            mode=mode,
            own_priority=own_priority,
            peer_priority=peer_priority,
            blocked_attempts=self.blocked_attempts.get(blocked_cell, 0),
            side_step_available=self._choose_side_step(blocked_cell) is not None,
        )

    def _commit_yield(self, robot_id: str, point: Point) -> None:
        """Фиксирует уступку другому роботу."""
        self.yielding_to = robot_id
        self.yield_cell = point
        self.yield_until = time.monotonic() + max(self.step_delay * 5, 0.45)

    def _is_yielding_for(self, point: Point) -> bool:
        """Проверяет, уступается ли сейчас эта клетка."""
        self._clear_expired_yield()
        return self.yield_cell == point and self.yielding_to is not None

    def _is_yielding_to(self, robot_id: str, point: Point) -> bool:
        """Проверяет, уступает ли робот другому роботу."""
        self._clear_expired_yield()
        return self.yielding_to == robot_id and self.yield_cell == point

    def _clear_expired_yield(self) -> None:
        """Сбрасывает истекшее обязательство уступить."""
        if self.yielding_to is not None and time.monotonic() >= self.yield_until:
            self.yielding_to = None
            self.yield_cell = None
            self.yield_until = 0.0

    async def _try_side_step(
        self,
        blocked_cell: Point,
        mode: str,
        task: Optional[WarehouseTask] = None,
        after_pickup: bool = False,
    ) -> bool:
        """Пробует безопасный обход заблокированной клетки."""
        if not self._should_side_step(blocked_cell):
            return False
        side_step = self._choose_side_step(blocked_cell)
        if side_step is None:
            return False
        if task is not None and not self._has_safe_energy_after_step(
            task,
            side_step,
            after_pickup=after_pickup,
        ):
            return False
        if self.battery < self.energy_per_step:
            return False
        if not await self._claim_next_cell(side_step, f"yield {mode}"):
            return False
        self.position = side_step
        self.battery = max(0.0, self.battery - self.energy_per_step)
        self.blocked_attempts.pop(blocked_cell, None)
        await self.publish_status(f"yielding {mode}")
        await self.sleep(self.step_delay)
        return True

    def _should_side_step(self, blocked_cell: Point) -> bool:
        """Проверяет, разрешает ли повторная блокировка обход."""
        if self.blocked_attempts.get(blocked_cell, 0) >= self.side_step_threshold:
            return True
        return any(
            point == blocked_cell and count >= self.side_step_threshold
            for (_, point), count in self.conflict_counts.items()
        )

    def _choose_side_step(self, blocked_cell: Point) -> Optional[Point]:
        """Выбирает клетку для обхода."""
        occupied = set(self._dynamic_blocks())
        requested = {
            requested
            for _, requested, _, _ in self.peer_intents.values()
        }
        candidates = [
            point
            for point in self.world.neighbors(self.position)
            if point != blocked_cell
            and point not in occupied
            and point not in requested
            and not self.world.is_service_cell(point)
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda point: (
                point.distance_to(blocked_cell),
                self.blocked_attempts.get(point, 0),
                point.y,
                point.x,
            ),
        )

    def _path_to(
        self,
        start: Point,
        target: Point,
        avoid_peers: bool = True,
    ) -> Optional[List[Point]]:
        """Находит путь к целевой клетке."""
        blocked = self._dynamic_blocks() if avoid_peers else []
        if avoid_peers and target in blocked and not self.world.is_service_cell(target):
            return None
        path = plan_path(self.world, start, target, blocked=blocked)
        if start != target and not path:
            return None
        return path

    def _task_route(
        self,
        task: WarehouseTask,
        start: Optional[Point] = None,
    ) -> Optional[List[Point]]:
        """Строит полный маршрут для задачи."""
        pickup_access, to_pickup = self._best_path_to_any(
            self.world.access_points_for(task.pickup),
            avoid_peers=False,
            start=start or self.position,
        )
        if pickup_access is None or to_pickup is None:
            return None
        from_pickup = self._path_to(pickup_access, task.dropoff, avoid_peers=False)
        if from_pickup is None:
            return None
        return [*to_pickup, *from_pickup]

    def _best_path_to_any(
        self,
        targets: Tuple[Point, ...],
        avoid_peers: bool,
        start: Optional[Point] = None,
    ) -> Tuple[Optional[Point], Optional[List[Point]]]:
        """Находит кратчайший путь к любой цели."""
        origin = start or self.position
        candidates: List[Tuple[int, int, int, Point, List[Point]]] = []
        for target in targets:
            path = self._path_to(origin, target, avoid_peers=avoid_peers)
            if path is not None:
                candidates.append((len(path), target.y, target.x, target, path))
        if not candidates:
            return None, None
        _, _, _, target, path = min(candidates)
        return target, path

    def _has_delivery_energy(self, dropoff: Point) -> bool:
        """Проверяет, хватает ли энергии на доставку."""
        to_dropoff = self._path_to(self.position, dropoff, avoid_peers=False)
        if to_dropoff is None:
            return False
        station_paths = [
            path
            for station in self.world.charging_stations
            for path in [self._path_to(dropoff, station, avoid_peers=False)]
            if path is not None
        ]
        if not station_paths:
            return False
        required_steps = len(to_dropoff) + min(len(path) for path in station_paths)
        required_steps += self.reserve_steps
        return (
            self.battery
            >= required_steps * self.energy_per_step + self.traffic_energy_margin
        )

    async def _fail_task(self, task: WarehouseTask, reason: str) -> None:
        """Сообщает о провале задачи и освобождает робота."""
        self.busy = False
        self.current_task_id = None
        await self.send(
            "OrderAgent",
            TASK_FAILED,
            TaskFailed(
                order_id=task.order_id,
                robot_id=self.agent_id,
                reason=reason,
            ),
        )
        await self.publish_status("idle")

    def _dynamic_blocks(self) -> List[Point]:
        """Возвращает позиции других роботов, блокирующие движение."""
        return [
            position
            for position in self.peer_positions.values()
            if not self.world.is_service_cell(position)
        ]

    def _handle_peer_status(self, status: RobotStatus) -> None:
        """Обрабатывает сообщение статуса другого робота."""
        if not isinstance(status, RobotStatus) or status.robot_id == self.agent_id:
            return
        self.peer_positions[status.robot_id] = status.position

    def _handle_peer_stuck(self, report: RobotStuck) -> None:
        """Обрабатывает сообщение застревания другого робота."""
        if not isinstance(report, RobotStuck) or report.robot_id == self.agent_id:
            return
        self.peer_positions[report.robot_id] = report.position

    def _handle_cell_request(self, request: CellRequest, created_at: float) -> None:
        """Обрабатывает сообщение запроса клетки."""
        if not isinstance(request, CellRequest) or request.robot_id == self.agent_id:
            return
        self.peer_intents[request.robot_id] = (
            request.current,
            request.requested,
            created_at,
            request.priority,
        )

    def _prune_peer_intents(self) -> None:
        """Удаляет устаревшие намерения движения других роботов."""
        cutoff = time.monotonic() - max(self.step_delay * 4, 0.4)
        self.peer_intents = {
            robot_id: intent
            for robot_id, intent in self.peer_intents.items()
            if intent[2] >= cutoff
        }

    async def publish_status(self, mode: str) -> None:
        """Публикует текущий статус робота."""
        await self.broadcast(
            ROBOT_STATUS,
            RobotStatus(
                robot_id=self.agent_id,
                position=self.position,
                battery=self.battery,
                mode=mode,
            ),
        )

    async def _become_stuck(self, reason: str) -> None:
        """Помечает робота застрявшим и сообщает об этом."""
        self.stuck = True
        self.busy = False
        self.charging = False
        self.waiting_for_charge = False
        self.current_task_id = None
        await self.broadcast(
            ROBOT_STUCK,
            RobotStuck(
                robot_id=self.agent_id,
                position=self.position,
                battery=self.battery,
                reason=reason,
            ),
        )
        await self.publish_status("stuck")


class ChargingStationAgent(BaseAgent):
    """Управляет распределением зарядок между роботами."""
    def __init__(
        self,
        bus: MessageBus,
        stations: List[Point],
        clock: Optional[SimulationClock] = None,
    ) -> None:
        """Инициализирует экземпляр."""
        super().__init__("ChargingStationAgent", bus, clock=clock)
        self.stations = stations
        self.occupied: Dict[str, Point] = {}
        self.waiting: Deque[ChargeRequest] = deque()

    async def run(self) -> None:
        """Запускает основной цикл."""
        while self.running and self.inbox is not None:
            message = await self.inbox.get()
            if message.topic == CHARGE_REQUESTED:
                await self._handle_charge_request(message.payload)
            elif message.topic == CHARGE_FINISHED:
                await self._handle_charge_finished(message.payload)
            elif message.topic == ROBOT_STUCK:
                await self._handle_robot_stuck(message.payload)

    async def _handle_charge_request(self, request: ChargeRequest) -> None:
        """Обрабатывает сообщение запроса зарядки."""
        station = self._nearest_free_station(request.position)
        if station is None:
            self.waiting.append(request)
            await self.send(
                request.robot_id,
                CHARGE_GRANTED,
                ChargeGrant(
                    robot_id=request.robot_id,
                    accepted=False,
                    station=None,
                    message="queued",
                ),
            )
            return

        await self._grant_station(request.robot_id, station)

    async def _handle_charge_finished(self, report: ChargeFinished) -> None:
        """Обрабатывает сообщение завершения зарядки."""
        self.occupied.pop(report.robot_id, None)
        await self._grant_next_waiting()

    async def _handle_robot_stuck(self, report: RobotStuck) -> None:
        """Обрабатывает сообщение застревания робота."""
        if not isinstance(report, RobotStuck):
            return
        self.occupied.pop(report.robot_id, None)
        self.waiting = deque(
            request for request in self.waiting if request.robot_id != report.robot_id
        )
        await self._grant_next_waiting()

    async def _grant_next_waiting(self) -> None:
        """Выдает зарядку следующему ожидающему."""
        while self.waiting:
            request = self.waiting.popleft()
            station = self._nearest_free_station(request.position)
            if station is None:
                self.waiting.appendleft(request)
                return
            await self._grant_station(request.robot_id, station)
            return

    async def _grant_station(self, robot_id: str, station: Point) -> None:
        """Выдает зарядную станцию."""
        self.occupied[robot_id] = station
        await self.send(
            robot_id,
            CHARGE_GRANTED,
            ChargeGrant(
                robot_id=robot_id,
                accepted=True,
                station=station,
                message=f"go to {station.label}",
            ),
        )

    def _nearest_free_station(self, position: Point) -> Optional[Point]:
        """Находит ближайшую свободную зарядную станцию."""
        busy_stations = set(self.occupied.values())
        free = [station for station in self.stations if station not in busy_stations]
        if not free:
            return None
        return min(free, key=lambda station: position.distance_to(station))

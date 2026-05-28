from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .agents import BaseAgent, ChargingStationAgent, OrderAgent, RobotAgent
from .bus import MessageBus
from .learning import ConflictQPolicy
from .metrics import MetricsRecorder, NullMetricsRecorder
from .messages import Point
from .world import WarehouseWorld


@dataclass
class Simulation:
    world: WarehouseWorld
    bus: MessageBus
    order_agent: OrderAgent
    charging_agent: ChargingStationAgent
    robots: List[RobotAgent]
    agents: List[BaseAgent]


def build_simulation(
    *,
    order_interval: float = 1.8,
    bid_window: float = 0.6,
    max_orders: int = 12,
    robot_count: int = 3,
    seed: int = 7,
    step_delay: float = 0.2,
    max_auction_retries: int = 3,
    learning_enabled: bool = False,
    learning_dir: str = "learning_state",
    extra_agents: Optional[Sequence[BaseAgent]] = None,
    extra_agent_factories: Optional[
        Sequence[Callable[[MessageBus, WarehouseWorld], BaseAgent]]
    ] = None,
) -> Simulation:
    world = WarehouseWorld.demo()
    bus = MessageBus()
    order_agent = OrderAgent(
        bus=bus,
        world=world,
        order_interval=order_interval,
        bid_window=bid_window,
        max_orders=max_orders,
        seed=seed,
        max_auction_retries=max_auction_retries,
    )
    charging_agent = ChargingStationAgent(
        bus=bus,
        stations=list(world.charging_stations),
    )
    starts = [Point(0, 9), Point(4, 9), Point(9, 4), Point(5, 0)]
    learning_root = Path(learning_dir)
    conflict_policy = ConflictQPolicy(
        learning_root / "conflict_policy.json",
        enabled=learning_enabled,
        seed=seed,
    )
    metrics = (
        MetricsRecorder(learning_root / "metrics.jsonl", enabled=True)
        if learning_enabled
        else NullMetricsRecorder()
    )
    robots = [
        RobotAgent(
            agent_id=f"R{index + 1}",
            bus=bus,
            world=world,
            position=starts[index % len(starts)],
            battery=max(30.0, 90.0 - index * 18.0),
            step_delay=step_delay,
            conflict_policy=conflict_policy,
            metrics=metrics,
        )
        for index in range(robot_count)
    ]
    agents: List[BaseAgent] = []
    if extra_agent_factories:
        agents.extend(factory(bus, world) for factory in extra_agent_factories)
    if extra_agents:
        agents.extend(extra_agents)
    agents.extend([order_agent, charging_agent, *robots])
    return Simulation(
        world=world,
        bus=bus,
        order_agent=order_agent,
        charging_agent=charging_agent,
        robots=robots,
        agents=agents,
    )

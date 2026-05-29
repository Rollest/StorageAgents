import argparse
import asyncio

from storage_agents.renderer import ConsoleRenderer
from storage_agents.simulation import build_simulation


async def run_simulation(args: argparse.Namespace) -> None:
    """Запускает консольную симуляцию."""
    simulation = build_simulation(
        order_interval=args.order_interval,
        bid_window=args.bid_window,
        max_orders=args.orders,
        robot_count=args.robots,
        seed=args.seed,
        step_delay=args.step_delay,
        max_auction_retries=args.max_auction_retries,
        learning_enabled=args.learning,
        metrics_enabled=args.metrics,
        learning_dir=args.learning_dir,
        time_scale=args.time_scale,
    )

    for agent in simulation.agents:
        await agent.start()

    renderer = ConsoleRenderer(
        world=simulation.world,
        order_agent=simulation.order_agent,
        robots=simulation.robots,
        charging_agent=simulation.charging_agent,
        bus=simulation.bus,
        tick=args.render_tick,
        clear_screen=not args.no_clear,
    )

    try:
        await renderer.run(args.duration)
    finally:
        for agent in reversed(simulation.agents):
            await agent.stop()


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы CLI."""
    parser = argparse.ArgumentParser(
        description="Run a multi-agent warehouse task allocation demo."
    )
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--orders", type=int, default=12)
    parser.add_argument("--robots", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--order-interval", type=float, default=1.8)
    parser.add_argument("--bid-window", type=float, default=0.6)
    parser.add_argument("--step-delay", type=float, default=0.2)
    parser.add_argument("--max-auction-retries", type=int, default=3)
    parser.add_argument("--learning", action="store_true")
    parser.add_argument("--metrics", action="store_true")
    parser.add_argument("--learning-dir", default="learning_state")
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--render-tick", type=float, default=0.25)
    parser.add_argument("--no-clear", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run_simulation(parse_args()))

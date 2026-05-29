import argparse
import asyncio
from pathlib import Path

from storage_agents.simulation import build_simulation
from storage_agents.web_server import start_web_server
from storage_agents.web_state import WebStateAgent


async def run_web(args: argparse.Namespace) -> None:
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
        extra_agent_factories=[
            lambda bus, world, clock: WebStateAgent(bus, world, clock=clock)
        ],
    )
    state_agent = simulation.agents[0]
    if not isinstance(state_agent, WebStateAgent):
        raise RuntimeError("web state agent was not created")

    for agent in simulation.agents:
        await agent.start()

    static_dir = Path(__file__).parent / "web"
    server, _ = start_web_server(
        args.host,
        args.port,
        state_agent,
        static_dir,
        simulation.clock,
    )
    print(f"Web visualization: http://{args.host}:{args.port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        await asyncio.Event().wait()
    finally:
        server.shutdown()
        server.server_close()
        for agent in reversed(simulation.agents):
            await agent.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the web visualization for the warehouse agents."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--orders", type=int, default=1000)
    parser.add_argument("--robots", type=int, default=4)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--order-interval", type=float, default=1.6)
    parser.add_argument("--bid-window", type=float, default=0.75)
    parser.add_argument("--step-delay", type=float, default=0.14)
    parser.add_argument("--max-auction-retries", type=int, default=3)
    parser.add_argument("--learning", action="store_true")
    parser.add_argument("--metrics", action="store_true")
    parser.add_argument("--learning-dir", default="learning_state")
    parser.add_argument("--time-scale", type=float, default=1.0)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run_web(parse_args()))
    except KeyboardInterrupt:
        print("Stopped.")

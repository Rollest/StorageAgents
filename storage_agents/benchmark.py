import argparse
import asyncio
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from .simulation import Simulation, build_simulation


@dataclass(frozen=True)
class BenchmarkConfig:
    seeds: Sequence[int]
    duration: float
    orders: int
    robots: int
    order_interval: float
    bid_window: float
    step_delay: float
    max_auction_retries: int
    learning_dir: str


@dataclass(frozen=True)
class BenchmarkResult:
    mode: str
    seed: int
    completed: int
    expired: int
    failed: int
    total_orders: int
    avg_task_seconds: float
    reassigned: int
    conflicts: int
    side_steps: int
    waits: int
    positive_rewards: int
    negative_rewards: int

    @property
    def completion_rate(self) -> float:
        if self.total_orders == 0:
            return 0.0
        return self.completed / self.total_orders


@dataclass(frozen=True)
class BenchmarkSummary:
    mode: str
    runs: int
    completed: float
    expired: float
    failed: float
    completion_rate: float
    avg_task_seconds: float
    conflicts: float
    side_steps: float
    waits: float
    positive_rewards: float
    negative_rewards: float


async def run_benchmark(config: BenchmarkConfig) -> List[BenchmarkResult]:
    learning_root = Path(config.learning_dir)
    if learning_root.exists():
        shutil.rmtree(learning_root)
    learning_root.mkdir(parents=True, exist_ok=True)

    results: List[BenchmarkResult] = []
    for seed in config.seeds:
        results.append(
            await _run_once(
                "baseline",
                seed,
                config,
                learning_enabled=False,
                metrics_enabled=True,
                learning_dir=None,
            )
        )
    for seed in config.seeds:
        results.append(
            await _run_once(
                "learning_cold",
                seed,
                config,
                learning_enabled=True,
                metrics_enabled=True,
                learning_dir=learning_root,
            )
        )
    for seed in config.seeds:
        results.append(
            await _run_once(
                "learning_warm",
                seed,
                config,
                learning_enabled=True,
                metrics_enabled=True,
                learning_dir=learning_root,
            )
        )
    return results


def summarize_results(results: Iterable[BenchmarkResult]) -> List[BenchmarkSummary]:
    grouped: dict[str, List[BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(result.mode, []).append(result)
    return [
        BenchmarkSummary(
            mode=mode,
            runs=len(items),
            completed=_avg(item.completed for item in items),
            expired=_avg(item.expired for item in items),
            failed=_avg(item.failed for item in items),
            completion_rate=_avg(item.completion_rate for item in items),
            avg_task_seconds=_avg(item.avg_task_seconds for item in items),
            conflicts=_avg(item.conflicts for item in items),
            side_steps=_avg(item.side_steps for item in items),
            waits=_avg(item.waits for item in items),
            positive_rewards=_avg(item.positive_rewards for item in items),
            negative_rewards=_avg(item.negative_rewards for item in items),
        )
        for mode, items in grouped.items()
    ]


def format_summary_table(summaries: Sequence[BenchmarkSummary]) -> str:
    headers = (
        "mode",
        "runs",
        "completed",
        "expired",
        "failed",
        "completion",
        "avg_task",
        "conflicts",
        "side_steps",
        "waits",
    )
    rows = [
        (
            summary.mode,
            str(summary.runs),
            f"{summary.completed:.1f}",
            f"{summary.expired:.1f}",
            f"{summary.failed:.1f}",
            f"{summary.completion_rate * 100:.1f}%",
            f"{summary.avg_task_seconds:.2f}s",
            f"{summary.conflicts:.1f}",
            f"{summary.side_steps:.1f}",
            f"{summary.waits:.1f}",
        )
        for summary in summaries
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )
    return "\n".join(lines)


def write_results_json(path: Path, results: Sequence[BenchmarkResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(result) for result in results]
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )


async def _run_once(
    mode: str,
    seed: int,
    config: BenchmarkConfig,
    *,
    learning_enabled: bool,
    metrics_enabled: bool,
    learning_dir: Path | None,
) -> BenchmarkResult:
    if learning_dir is None:
        temp_context = tempfile.TemporaryDirectory()
        run_learning_dir = Path(temp_context.name)
    else:
        temp_context = None
        run_learning_dir = learning_dir
    metrics_path = run_learning_dir / "metrics.jsonl"
    if metrics_path.exists():
        metrics_path.unlink()

    simulation = build_simulation(
        order_interval=config.order_interval,
        bid_window=config.bid_window,
        max_orders=config.orders,
        robot_count=config.robots,
        seed=seed,
        step_delay=config.step_delay,
        max_auction_retries=config.max_auction_retries,
        learning_enabled=learning_enabled,
        metrics_enabled=metrics_enabled,
        learning_dir=str(run_learning_dir),
    )
    try:
        await _run_headless(simulation, config.duration)
        metrics = _read_learning_metrics(metrics_path)
        return BenchmarkResult(
            mode=mode,
            seed=seed,
            completed=simulation.order_agent.completed_count,
            expired=simulation.order_agent.expired_count,
            failed=simulation.order_agent.failed_count,
            total_orders=len(simulation.order_agent.orders),
            avg_task_seconds=simulation.order_agent.average_delivery_seconds,
            reassigned=simulation.order_agent.reassigned_count,
            conflicts=metrics["conflicts"],
            side_steps=metrics["side_steps"],
            waits=metrics["waits"],
            positive_rewards=metrics["positive_rewards"],
            negative_rewards=metrics["negative_rewards"],
        )
    finally:
        if temp_context is not None:
            temp_context.cleanup()


async def _run_headless(simulation: Simulation, duration: float) -> None:
    for agent in simulation.agents:
        await agent.start()
    try:
        await asyncio.sleep(duration)
    finally:
        for agent in reversed(simulation.agents):
            await agent.stop()


def _read_learning_metrics(path: Path) -> dict[str, int]:
    totals = {
        "conflicts": 0,
        "side_steps": 0,
        "waits": 0,
        "positive_rewards": 0,
        "negative_rewards": 0,
    }
    if not path.exists():
        return totals
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "conflict_action":
            totals["conflicts"] += 1
            if event.get("action") == "side_step":
                totals["side_steps"] += 1
            if event.get("action") == "wait":
                totals["waits"] += 1
        elif event.get("type") == "conflict_reward":
            reward = float(event.get("reward", 0.0))
            if reward > 0:
                totals["positive_rewards"] += 1
            elif reward < 0:
                totals["negative_rewards"] += 1
    return totals


def _avg(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def parse_benchmark_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare baseline vs learning conflict policies."
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--orders", type=int, default=100)
    parser.add_argument("--robots", type=int, default=4)
    parser.add_argument("--order-interval", type=float, default=0.35)
    parser.add_argument("--bid-window", type=float, default=0.18)
    parser.add_argument("--step-delay", type=float, default=0.02)
    parser.add_argument("--max-auction-retries", type=int, default=2)
    parser.add_argument("--learning-dir", default="learning_state_benchmark")
    parser.add_argument("--json-out", default="")
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> BenchmarkConfig:
    return BenchmarkConfig(
        seeds=args.seeds,
        duration=args.duration,
        orders=args.orders,
        robots=args.robots,
        order_interval=args.order_interval,
        bid_window=args.bid_window,
        step_delay=args.step_delay,
        max_auction_retries=args.max_auction_retries,
        learning_dir=args.learning_dir,
    )

import asyncio
from pathlib import Path

from storage_agents.benchmark import (
    config_from_args,
    format_summary_table,
    parse_benchmark_args,
    run_benchmark,
    summarize_results,
    write_results_json,
)


async def main() -> None:
    """Запускает консольную точку входа."""
    args = parse_benchmark_args()
    config = config_from_args(args)
    results = await run_benchmark(config)
    summaries = summarize_results(results)
    print(format_summary_table(summaries))
    if args.json_out:
        write_results_json(Path(args.json_out), results)
        print(f"\nWrote detailed results to {args.json_out}")


if __name__ == "__main__":
    asyncio.run(main())

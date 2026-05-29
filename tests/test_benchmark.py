import unittest

from storage_agents.benchmark import (
    BenchmarkResult,
    BenchmarkSummary,
    format_summary_table,
    summarize_results,
)


class BenchmarkTests(unittest.TestCase):
    def test_summarize_results_averages_by_mode(self) -> None:
        results = [
            BenchmarkResult(
                mode="baseline",
                seed=1,
                completed=10,
                expired=2,
                failed=1,
                total_orders=13,
                avg_task_seconds=2.0,
                reassigned=0,
                conflicts=20,
                side_steps=3,
                waits=17,
                positive_rewards=2,
                negative_rewards=5,
            ),
            BenchmarkResult(
                mode="baseline",
                seed=2,
                completed=14,
                expired=0,
                failed=0,
                total_orders=14,
                avg_task_seconds=4.0,
                reassigned=1,
                conflicts=10,
                side_steps=1,
                waits=9,
                positive_rewards=4,
                negative_rewards=1,
            ),
        ]

        summary = summarize_results(results)[0]

        self.assertEqual(summary.mode, "baseline")
        self.assertEqual(summary.runs, 2)
        self.assertEqual(summary.completed, 12)
        self.assertEqual(summary.expired, 1)
        self.assertEqual(summary.failed, 0.5)
        self.assertEqual(summary.avg_task_seconds, 3)

    def test_format_summary_table_contains_key_columns(self) -> None:
        table = format_summary_table(
            [
                BenchmarkSummary(
                    mode="learning_warm",
                    runs=3,
                    completed=42,
                    expired=2,
                    failed=0,
                    completion_rate=0.91,
                    avg_task_seconds=2.5,
                    conflicts=12,
                    side_steps=4,
                    waits=8,
                    positive_rewards=6,
                    negative_rewards=3,
                )
            ]
        )

        self.assertIn("mode", table)
        self.assertIn("completion", table)
        self.assertIn("learning_warm", table)
        self.assertIn("91.0%", table)

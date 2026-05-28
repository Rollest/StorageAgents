import json
import tempfile
import unittest
from pathlib import Path

from storage_agents.learning import (
    CONFLICT_ACTION_SIDE_STEP,
    CONFLICT_ACTION_WAIT,
    ConflictLearningState,
    ConflictQPolicy,
)
from storage_agents.metrics import MetricsRecorder


class LearningPolicyTests(unittest.TestCase):
    def test_q_policy_persists_updates_to_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "conflict_policy.json"
            state = ConflictLearningState(
                battery_bucket="medium",
                mode_bucket="pickup",
                peer_priority_bucket="high",
                own_priority_bucket="medium",
                blocked_bucket="stuck",
                side_step_available=True,
            )
            policy = ConflictQPolicy(path, enabled=True, epsilon=0.0)

            policy.update(state, CONFLICT_ACTION_SIDE_STEP, reward=2.0)
            policy.save()

            loaded = ConflictQPolicy(path, enabled=True, epsilon=0.0)
            self.assertGreater(
                loaded.q[state.key()][CONFLICT_ACTION_SIDE_STEP],
                loaded.q[state.key()][CONFLICT_ACTION_WAIT],
            )

    def test_q_policy_uses_default_wait_when_learning_disabled(self) -> None:
        state = ConflictLearningState(
            battery_bucket="high",
            mode_bucket="idle",
            peer_priority_bucket="low",
            own_priority_bucket="low",
            blocked_bucket="fresh",
            side_step_available=True,
        )
        policy = ConflictQPolicy(enabled=False)

        self.assertEqual(policy.choose_action(state), CONFLICT_ACTION_WAIT)

    def test_metrics_recorder_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.jsonl"
            recorder = MetricsRecorder(path, enabled=True)

            recorder.record("conflict_action", robot="R1", action="wait")

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["type"], "conflict_action")
            self.assertEqual(payload["robot"], "R1")

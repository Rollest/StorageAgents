import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from .messages import Point


CONFLICT_ACTION_WAIT = "wait"
CONFLICT_ACTION_SIDE_STEP = "side_step"
CONFLICT_ACTIONS = (CONFLICT_ACTION_WAIT, CONFLICT_ACTION_SIDE_STEP)


@dataclass(frozen=True)
class ConflictLearningState:
    battery_bucket: str
    mode_bucket: str
    peer_priority_bucket: str
    own_priority_bucket: str
    blocked_bucket: str
    side_step_available: bool

    def key(self) -> str:
        return "|".join(
            (
                f"battery={self.battery_bucket}",
                f"mode={self.mode_bucket}",
                f"peer={self.peer_priority_bucket}",
                f"own={self.own_priority_bucket}",
                f"blocked={self.blocked_bucket}",
                f"side_step={'yes' if self.side_step_available else 'no'}",
            )
        )


class ConflictStateEncoder:
    def encode(
        self,
        *,
        battery: float,
        mode: str,
        own_priority: float,
        peer_priority: float,
        blocked_attempts: int,
        side_step_available: bool,
    ) -> ConflictLearningState:
        return ConflictLearningState(
            battery_bucket=self._battery_bucket(battery),
            mode_bucket=self._mode_bucket(mode),
            own_priority_bucket=self._priority_bucket(own_priority),
            peer_priority_bucket=self._priority_bucket(peer_priority),
            blocked_bucket=self._blocked_bucket(blocked_attempts),
            side_step_available=side_step_available,
        )

    def _battery_bucket(self, battery: float) -> str:
        if battery < 25:
            return "critical"
        if battery < 45:
            return "low"
        if battery < 70:
            return "medium"
        return "high"

    def _mode_bucket(self, mode: str) -> str:
        lowered = mode.lower()
        if "charger" in lowered or "charging" in lowered:
            return "charging"
        if "deliver" in lowered:
            return "deliver"
        if "pickup" in lowered:
            return "pickup"
        if "yield" in lowered:
            return "yield"
        return "idle"

    def _priority_bucket(self, priority: float) -> str:
        if priority < 2:
            return "low"
        if priority < 5:
            return "medium"
        return "high"

    def _blocked_bucket(self, attempts: int) -> str:
        if attempts <= 1:
            return "fresh"
        if attempts <= 3:
            return "repeated"
        return "stuck"


class ConflictQPolicy:
    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        enabled: bool = False,
        alpha: float = 0.25,
        gamma: float = 0.75,
        epsilon: float = 0.03,
        seed: int = 7,
        save_every: int = 20,
    ) -> None:
        self.path = path
        self.enabled = enabled
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.random = random.Random(seed)
        self.save_every = save_every
        self.update_count = 0
        self.q: Dict[str, Dict[str, float]] = {}
        if self.path is not None:
            self.load()

    def choose_action(
        self,
        state: ConflictLearningState,
        *,
        allowed_actions: tuple[str, ...] = CONFLICT_ACTIONS,
    ) -> str:
        if not self.enabled or not allowed_actions:
            return self._default_action(state, allowed_actions)
        values = self._values_for(state)
        if all(abs(values.get(action, 0.0)) < 1e-9 for action in allowed_actions):
            return self._default_action(state, allowed_actions)
        if self.random.random() < self.epsilon:
            return self.random.choice(allowed_actions)
        return max(
            allowed_actions,
            key=lambda action: (values.get(action, 0.0), -allowed_actions.index(action)),
        )

    def update(
        self,
        state: ConflictLearningState,
        action: str,
        reward: float,
        next_state: Optional[ConflictLearningState] = None,
    ) -> None:
        if not self.enabled or action not in CONFLICT_ACTIONS:
            return
        values = self._values_for(state)
        current = values.get(action, 0.0)
        future = 0.0
        if next_state is not None:
            future_values = self._values_for(next_state)
            future = max(future_values.values(), default=0.0)
        values[action] = current + self.alpha * (reward + self.gamma * future - current)
        self.update_count += 1
        if self.path is not None and self.update_count % self.save_every == 0:
            self.save()

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        raw_q = payload.get("q", payload)
        if isinstance(raw_q, Mapping):
            self.q = {
                str(state): {
                    str(action): float(value)
                    for action, value in actions.items()
                    if action in CONFLICT_ACTIONS
                }
                for state, actions in raw_q.items()
                if isinstance(actions, Mapping)
            }

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "alpha": self.alpha,
            "gamma": self.gamma,
            "epsilon": self.epsilon,
            "q": self.q,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _values_for(self, state: ConflictLearningState) -> Dict[str, float]:
        return self.q.setdefault(
            state.key(),
            {action: 0.0 for action in CONFLICT_ACTIONS},
        )

    def _default_action(
        self,
        state: ConflictLearningState,
        allowed_actions: tuple[str, ...],
    ) -> str:
        if state.side_step_available and state.blocked_bucket == "stuck":
            return CONFLICT_ACTION_SIDE_STEP
        if CONFLICT_ACTION_WAIT in allowed_actions:
            return CONFLICT_ACTION_WAIT
        return allowed_actions[0]


def point_key(point: Point) -> str:
    return f"{point.x}:{point.y}"

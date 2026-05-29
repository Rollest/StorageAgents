import json
import threading
import time
from pathlib import Path
from typing import Optional


class MetricsRecorder:
    """Записывает метрики симуляции как JSONL-события."""
    def __init__(self, path: Optional[Path] = None, enabled: bool = False) -> None:
        """Инициализирует экземпляр."""
        self.path = path
        self.enabled = enabled and path is not None
        self._lock = threading.Lock()
        if self.enabled and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, **fields: object) -> None:
        """Записывает событие метрик."""
        if not self.enabled or self.path is None:
            return
        payload = {
            "t": round(time.time(), 3),
            "type": event_type,
            **fields,
        }
        line = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class NullMetricsRecorder(MetricsRecorder):
    """Отбрасывает метрики, когда запись отключена."""
    def __init__(self) -> None:
        """Инициализирует экземпляр."""
        super().__init__(None, enabled=False)

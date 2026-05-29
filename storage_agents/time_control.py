import asyncio
import threading
import time


class SimulationClock:
    """Общий таймер симуляции с настраиваемой скоростью времени."""

    def __init__(self, speed: float = 1.0) -> None:
        """Инициализирует экземпляр."""
        self._lock = threading.RLock()
        self._speed = self._clamp_speed(speed)
        self._real_anchor = time.monotonic()
        self._sim_anchor = 0.0

    @property
    def speed(self) -> float:
        """Возвращает текущую скорость симуляции."""
        with self._lock:
            return self._speed

    def set_speed(self, speed: float) -> float:
        """Задает скорость симуляции."""
        with self._lock:
            now = time.monotonic()
            self._sim_anchor += (now - self._real_anchor) * self._speed
            self._real_anchor = now
            self._speed = self._clamp_speed(speed)
            return self._speed

    def elapsed(self) -> float:
        """Возвращает прошедшие секунды симуляции."""
        with self._lock:
            return self._sim_anchor + (time.monotonic() - self._real_anchor) * self._speed

    async def sleep(self, seconds: float) -> None:
        """Ожидает в симуляционном времени."""
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        target = self.elapsed() + seconds
        while True:
            remaining = target - self.elapsed()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining / max(self.speed, 0.01), 0.05))

    def snapshot(self) -> dict[str, float]:
        """Возвращает сериализуемый снимок состояния."""
        return {
            "speed": round(self.speed, 2),
            "elapsedSeconds": round(self.elapsed(), 1),
        }

    def _clamp_speed(self, speed: float) -> float:
        """Ограничивает скорость поддерживаемым диапазоном."""
        return max(0.1, min(10.0, float(speed)))

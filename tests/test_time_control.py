import unittest

from storage_agents.time_control import SimulationClock


class SimulationClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_speed_changes_elapsed_simulation_time(self) -> None:
        clock = SimulationClock(speed=1.0)
        clock.set_speed(4.0)

        await clock.sleep(0.04)

        self.assertGreaterEqual(clock.elapsed(), 0.04)

    def test_speed_is_clamped_to_safe_range(self) -> None:
        clock = SimulationClock(speed=100.0)

        self.assertEqual(clock.speed, 10.0)
        self.assertEqual(clock.set_speed(0.0), 0.1)

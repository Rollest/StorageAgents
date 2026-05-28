import asyncio
import unittest

from storage_agents.simulation import build_simulation


class SimulationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_orders_reach_terminal_state_in_short_run(self) -> None:
        simulation = build_simulation(
            order_interval=0.001,
            bid_window=0.001,
            max_orders=1,
            robot_count=3,
            seed=7,
            step_delay=0.001,
        )

        for agent in simulation.agents:
            await agent.start()

        try:
            await asyncio.sleep(1.0)
        finally:
            for agent in reversed(simulation.agents):
                await agent.stop()

        statuses = {task.status for task in simulation.order_agent.orders.values()}
        self.assertTrue(statuses)
        self.assertLessEqual(statuses, {"completed", "expired", "failed"})

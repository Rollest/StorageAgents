import unittest
from dataclasses import FrozenInstanceError

from storage_agents.messages import Point, WarehouseTask
from storage_agents.world import WarehouseWorld


class MessageContractTests(unittest.TestCase):
    def test_warehouse_task_message_is_immutable_snapshot(self) -> None:
        task = WarehouseTask(
            order_id="O001",
            pickup=Point(1, 5),
            dropoff=WarehouseWorld.demo().packaging_zone,
        )

        with self.assertRaises(FrozenInstanceError):
            task.status = "in_progress"

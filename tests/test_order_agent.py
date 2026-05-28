import asyncio
import unittest

from storage_agents.agents import OrderAgent
from storage_agents.bus import MessageBus
from storage_agents.messages import (
    Bid,
    Point,
    RobotStuck,
    TASK_WAITING,
    TaskRejected,
    WarehouseTask,
)
from storage_agents.world import WarehouseWorld


class OrderAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_assigned_task_fails_when_robot_gets_stuck(self) -> None:
        world = WarehouseWorld.demo()
        order_agent = OrderAgent(MessageBus(), world)
        task = WarehouseTask(
            order_id="O001",
            pickup=Point(6, 2),
            dropoff=world.packaging_zone,
            status="in_progress",
            assigned_robot="R3",
        )
        order_agent.orders[task.order_id] = task

        await order_agent._handle_robot_stuck(
            RobotStuck(
                robot_id="R3",
                position=Point(6, 1),
                battery=4.6,
                reason="not enough battery",
            )
        )

        self.assertEqual(order_agent.orders[task.order_id].status, "failed")
        self.assertEqual(order_agent.failed_count, 1)
        self.assertEqual(order_agent.expired_count, 0)

    async def test_rejected_assignment_is_reassigned_to_next_bid(self) -> None:
        world = WarehouseWorld.demo()
        bus = MessageBus()
        robot_queue = await bus.subscribe("R2")
        order_agent = OrderAgent(bus, world)
        task = WarehouseTask(
            order_id="O001",
            pickup=Point(6, 2),
            dropoff=world.packaging_zone,
            status="assigned",
            assigned_robot="R1",
        )
        order_agent.orders[task.order_id] = task
        order_agent.pending_bids[task.order_id] = [
            Bid(
                order_id=task.order_id,
                robot_id="R2",
                eta_seconds=2.0,
                battery_after=70.0,
                score=2.0,
                note="ready",
            )
        ]

        await order_agent._handle_task_rejected(
            TaskRejected(
                order_id=task.order_id,
                robot_id="R1",
                reason="robot is busy",
            )
        )

        reassigned = order_agent.orders[task.order_id]
        self.assertEqual(reassigned.status, "assigned")
        self.assertEqual(reassigned.assigned_robot, "R2")
        self.assertEqual(order_agent.reassigned_count, 1)
        message = await asyncio.wait_for(robot_queue.get(), timeout=0.1)
        self.assertEqual(message.recipient, "R2")

    async def test_order_waits_for_retry_before_expiring_without_bids(self) -> None:
        world = WarehouseWorld.demo()
        bus = MessageBus()
        observer = await bus.subscribe("observer")
        order_agent = OrderAgent(bus, world, max_auction_retries=1)
        task = WarehouseTask(
            order_id="O001",
            pickup=Point(6, 2),
            dropoff=world.packaging_zone,
            status="bidding",
        )
        order_agent.orders[task.order_id] = task

        try:
            await order_agent._assign_best_bid(task.order_id)

            self.assertEqual(order_agent.orders[task.order_id].status, "waiting")
            self.assertEqual(order_agent.expired_count, 0)
            message = await asyncio.wait_for(observer.get(), timeout=0.1)
            self.assertEqual(message.topic, TASK_WAITING)
        finally:
            await order_agent.stop()

    async def test_order_expires_after_retry_budget_is_used(self) -> None:
        world = WarehouseWorld.demo()
        order_agent = OrderAgent(MessageBus(), world, max_auction_retries=0)
        task = WarehouseTask(
            order_id="O001",
            pickup=Point(6, 2),
            dropoff=world.packaging_zone,
            status="bidding",
        )
        order_agent.orders[task.order_id] = task

        await order_agent._assign_best_bid(task.order_id)

        self.assertEqual(order_agent.orders[task.order_id].status, "expired")
        self.assertEqual(order_agent.expired_count, 1)

    async def test_rejected_assignment_without_other_bids_retries_instead_of_failing(self) -> None:
        world = WarehouseWorld.demo()
        order_agent = OrderAgent(MessageBus(), world, max_auction_retries=1)
        task = WarehouseTask(
            order_id="O001",
            pickup=Point(6, 2),
            dropoff=world.packaging_zone,
            status="assigned",
            assigned_robot="R1",
        )
        order_agent.orders[task.order_id] = task

        try:
            await order_agent._handle_task_rejected(
                TaskRejected(
                    order_id=task.order_id,
                    robot_id="R1",
                    reason="robot is not available",
                )
            )

            self.assertEqual(order_agent.orders[task.order_id].status, "waiting")
            self.assertEqual(order_agent.failed_count, 0)
            self.assertEqual(order_agent.expired_count, 0)
        finally:
            await order_agent.stop()

    async def test_in_progress_rejection_retries_instead_of_failing(self) -> None:
        world = WarehouseWorld.demo()
        order_agent = OrderAgent(MessageBus(), world, max_auction_retries=1)
        task = WarehouseTask(
            order_id="O001",
            pickup=Point(6, 2),
            dropoff=world.packaging_zone,
            status="in_progress",
            assigned_robot="R1",
        )
        order_agent.orders[task.order_id] = task

        try:
            await order_agent._handle_task_rejected(
                TaskRejected(
                    order_id=task.order_id,
                    robot_id="R1",
                    reason="battery reserve would be unsafe",
                )
            )

            self.assertEqual(order_agent.orders[task.order_id].status, "waiting")
            self.assertEqual(order_agent.failed_count, 0)
        finally:
            await order_agent.stop()

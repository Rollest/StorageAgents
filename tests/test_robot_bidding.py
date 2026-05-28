import asyncio
import time
import unittest

from storage_agents.agents import RobotAgent
from storage_agents.bus import MessageBus
from storage_agents.messages import (
    CHARGE_REQUESTED,
    TASK_REJECTED,
    ChargeGrant,
    CellRequest,
    Point,
    WarehouseTask,
)
from storage_agents.world import WarehouseWorld


class RobotBiddingTests(unittest.IsolatedAsyncioTestCase):
    def test_robot_bids_when_it_can_finish_and_reach_charger(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(0, 9), battery=90.0)
        task = WarehouseTask("O001", pickup=Point(1, 5), dropoff=world.packaging_zone)

        bid = robot.make_bid_for(task)

        self.assertIsNotNone(bid)
        self.assertEqual(bid.robot_id, "R1")

    def test_robot_refuses_task_when_battery_is_not_enough(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(0, 9), battery=5.0)
        task = WarehouseTask("O001", pickup=Point(8, 8), dropoff=world.packaging_zone)

        self.assertIsNone(robot.make_bid_for(task))

    def test_robot_refuses_task_without_navigation_reserve(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(9, 0), battery=20.0)
        task = WarehouseTask("O001", pickup=Point(8, 1), dropoff=world.packaging_zone)

        self.assertIsNone(robot.make_bid_for(task))

    def test_robot_can_check_if_it_can_reach_target(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(0, 0), battery=2.5)

        self.assertFalse(robot.can_reach(Point(3, 0)))

    def test_robot_routes_around_occupied_corridor_to_service_cell(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(9, 4), battery=90.0)
        robot.peer_positions = {"R2": Point(9, 3)}

        path = robot._path_to(robot.position, world.packaging_zone)

        self.assertIsNotNone(path)
        self.assertNotEqual(path[0], Point(9, 3))
        self.assertNotIn(Point(9, 3), path)

    def test_robot_can_still_find_static_route_when_peer_temporarily_blocks_path(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(9, 2), battery=90.0)
        robot.peer_positions = {
            "R2": Point(8, 2),
            "R3": Point(9, 1),
            "R4": Point(9, 3),
        }

        dynamic_path = robot._path_to(robot.position, world.packaging_zone)
        static_path = robot._path_to(
            robot.position,
            world.packaging_zone,
            avoid_peers=False,
        )

        self.assertIsNone(dynamic_path)
        self.assertIsNotNone(static_path)

    def test_robot_chooses_alternate_shelf_access_when_nearest_is_blocked(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(7, 1), battery=90.0)
        robot.peer_positions = {"R3": Point(6, 1)}

        target, path = robot._best_path_to_any(
            world.access_points_for(Point(6, 2)),
            avoid_peers=True,
        )

        self.assertIsNotNone(path)
        self.assertNotEqual(target, Point(6, 1))
        self.assertNotIn(Point(6, 1), path)

    def test_robot_checks_delivery_energy_after_pickup(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(6, 1), battery=8.0)

        self.assertFalse(robot._has_delivery_energy(world.packaging_zone))

    def test_robot_uses_adaptive_workload_charge_decision(self) -> None:
        world = WarehouseWorld.demo()
        far_robot = RobotAgent("R1", MessageBus(), world, Point(0, 9), battery=40.6)
        near_work_robot = RobotAgent("R2", MessageBus(), world, Point(9, 0), battery=50.0)
        low_coverage_robot = RobotAgent("R3", MessageBus(), world, Point(9, 0), battery=28.8)

        self.assertTrue(far_robot.needs_charge_for_workload())
        self.assertFalse(near_work_robot.needs_charge_for_workload())
        self.assertTrue(low_coverage_robot.needs_charge_for_workload())

    def test_robot_can_bid_at_forty_percent_when_route_has_real_reserve(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(9, 0), battery=40.6)
        task = WarehouseTask("O001", pickup=Point(8, 1), dropoff=world.packaging_zone)

        self.assertIsNotNone(robot.make_bid_for(task))

    def test_robot_refuses_step_that_would_break_task_and_charger_reserve(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(9, 2), battery=45.0)
        task = WarehouseTask("O001", pickup=Point(8, 8), dropoff=world.packaging_zone)

        self.assertFalse(robot._has_safe_energy_after_step(task, Point(8, 2)))

    def test_right_of_way_priority_prefers_charging_and_delivery(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(4, 4), battery=90.0)

        idle = robot._movement_priority(Point(5, 4), "idle")
        pickup = robot._movement_priority(Point(5, 4), "pickup O001")
        delivery = robot._movement_priority(Point(5, 4), "deliver O001")
        charging = robot._movement_priority(Point(5, 4), "to charger")

        self.assertGreater(pickup, idle)
        self.assertGreater(delivery, pickup)
        self.assertGreater(charging, delivery)

    async def test_yield_commitment_prevents_immediate_reclaim(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(0, 0), battery=90.0)
        robot._commit_yield("R2", Point(1, 0))

        self.assertFalse(await robot._claim_next_cell(Point(1, 0), "idle"))

    async def test_head_on_conflict_yields_to_higher_priority_peer(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R2", MessageBus(), world, Point(0, 0), battery=90.0)
        robot._handle_cell_request(
            CellRequest(
                robot_id="R1",
                current=Point(1, 0),
                requested=Point(0, 0),
                mode="deliver O001",
                priority=5.0,
            ),
            created_at=time.monotonic(),
        )

        self.assertFalse(await robot._claim_next_cell(Point(1, 0), "idle"))
        self.assertEqual(robot.yielding_to, "R1")

    def test_side_step_chooses_free_neighbor_outside_blocked_cell(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(4, 4), battery=90.0)
        robot.peer_positions = {"R2": Point(3, 4)}

        side_step = robot._choose_side_step(Point(5, 4))

        self.assertIsNotNone(side_step)
        self.assertNotEqual(side_step, Point(5, 4))
        self.assertNotEqual(side_step, Point(3, 4))

    async def test_repeated_conflict_triggers_side_step_move(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(4, 4), battery=90.0)
        robot.blocked_attempts[Point(5, 4)] = robot.side_step_threshold

        moved = await robot._try_side_step(Point(5, 4), "pickup O001")

        self.assertTrue(moved)
        self.assertNotEqual(robot.position, Point(4, 4))
        self.assertNotEqual(robot.position, Point(5, 4))


class RobotChargingTests(unittest.IsolatedAsyncioTestCase):
    async def test_robot_becomes_stuck_when_granted_unreachable_charger(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(0, 0), battery=2.5)

        await robot._handle_charge_grant(
            ChargeGrant(
                robot_id="R1",
                accepted=True,
                station=Point(3, 0),
                message="go",
            )
        )

        self.assertTrue(robot.stuck)
        self.assertFalse(robot.charging)

    async def test_robot_gets_stuck_instead_of_requesting_unreachable_charge(self) -> None:
        world = WarehouseWorld.demo()
        robot = RobotAgent("R1", MessageBus(), world, Point(5, 5), battery=0.0)

        await robot.request_charge("test")

        self.assertTrue(robot.stuck)
        self.assertFalse(robot.waiting_for_charge)

    async def test_robot_aborts_task_and_requests_charge_before_unsafe_route(self) -> None:
        world = WarehouseWorld.demo()
        bus = MessageBus()
        order_queue = await bus.subscribe("OrderAgent")
        charging_queue = await bus.subscribe("ChargingStationAgent")
        robot = RobotAgent("R1", bus, world, Point(9, 2), battery=45.0)
        robot.busy = True
        robot.current_task_id = "O001"
        task = WarehouseTask("O001", pickup=Point(8, 8), dropoff=world.packaging_zone)

        await robot._abort_task_for_charge(task, "test reserve")

        rejected = await asyncio.wait_for(order_queue.get(), timeout=0.1)
        charge = None
        for _ in range(3):
            message = await asyncio.wait_for(charging_queue.get(), timeout=0.1)
            if message.topic == CHARGE_REQUESTED:
                charge = message
                break
        self.assertEqual(rejected.topic, TASK_REJECTED)
        self.assertIsNotNone(charge)
        self.assertFalse(robot.busy)
        self.assertTrue(robot.waiting_for_charge)

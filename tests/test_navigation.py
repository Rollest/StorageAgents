import unittest

from storage_agents.navigation import plan_path
from storage_agents.messages import Point
from storage_agents.world import WarehouseWorld


class NavigationTests(unittest.TestCase):
    def test_path_avoids_shelf_cells(self) -> None:
        world = WarehouseWorld.demo()

        path = plan_path(world, Point(1, 0), Point(1, 2))

        self.assertNotIn(Point(1, 1), path)
        self.assertEqual(path[-1], Point(1, 2))

    def test_shelf_access_point_is_walkable_neighbor(self) -> None:
        world = WarehouseWorld.demo()

        access = world.access_point_for(Point(1, 1), Point(0, 0))

        self.assertTrue(world.is_walkable(access))
        self.assertEqual(access.distance_to(Point(1, 1)), 1)

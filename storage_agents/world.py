from dataclasses import dataclass
from typing import Iterable, Tuple

from .messages import Point


@dataclass(frozen=True)
class WarehouseWorld:
    """Describes the warehouse grid and service cells."""
    width: int
    height: int
    shelves: Tuple[Point, ...]
    packaging_zone: Point
    charging_stations: Tuple[Point, ...]

    def in_bounds(self, point: Point) -> bool:
        """Checks whether a point is inside the grid."""
        return 0 <= point.x < self.width and 0 <= point.y < self.height

    def is_shelf(self, point: Point) -> bool:
        """Checks whether a point is a shelf."""
        return point in self.shelves

    def is_walkable(self, point: Point) -> bool:
        """Checks whether a point can be traversed."""
        return self.in_bounds(point) and not self.is_shelf(point)

    def is_service_cell(self, point: Point) -> bool:
        """Checks whether a point is a service cell."""
        return point == self.packaging_zone or point in self.charging_stations

    def neighbors(self, point: Point) -> Iterable[Point]:
        """Yields walkable neighboring cells."""
        candidates = (
            Point(point.x + 1, point.y),
            Point(point.x - 1, point.y),
            Point(point.x, point.y + 1),
            Point(point.x, point.y - 1),
        )
        return [candidate for candidate in candidates if self.is_walkable(candidate)]

    def access_point_for(self, shelf: Point, origin: Point) -> Point:
        """Chooses a shelf access point near an origin."""
        candidates = self.access_points_for(shelf)
        if not candidates:
            return shelf
        return min(candidates, key=lambda point: (origin.distance_to(point), point.y, point.x))

    def access_points_for(self, shelf: Point) -> Tuple[Point, ...]:
        """Returns all access points for a shelf."""
        return tuple(self.neighbors(shelf))

    @classmethod
    def demo(cls) -> "WarehouseWorld":
        """Builds the default demo warehouse."""
        shelves = (
            Point(1, 1),
            Point(2, 2),
            Point(4, 1),
            Point(6, 2),
            Point(8, 1),
            Point(1, 5),
            Point(3, 6),
            Point(5, 5),
            Point(7, 6),
            Point(8, 8),
        )
        return cls(
            width=10,
            height=10,
            shelves=shelves,
            packaging_zone=Point(9, 0),
            charging_stations=(Point(0, 0), Point(9, 9)),
        )

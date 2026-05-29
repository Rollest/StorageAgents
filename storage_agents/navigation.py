import heapq
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .messages import Point
from .world import WarehouseWorld


def plan_path(
    world: WarehouseWorld,
    start: Point,
    goal: Point,
    blocked: Optional[Iterable[Point]] = None,
) -> List[Point]:
    """Строит A* путь от старта к цели без стартовой клетки."""

    blocked_set: Set[Point] = set(blocked or ())
    blocked_set.discard(start)
    blocked_set.discard(goal)
    if start == goal:
        return []

    frontier: List[Tuple[int, int, Point]] = []
    heapq.heappush(frontier, (0, 0, start))
    came_from: Dict[Point, Optional[Point]] = {start: None}
    cost_so_far: Dict[Point, int] = {start: 0}
    sequence = 0

    while frontier:
        _, _, current = heapq.heappop(frontier)
        if current == goal:
            break

        for neighbor in world.neighbors(current):
            if neighbor in blocked_set:
                continue
            new_cost = cost_so_far[current] + 1
            if neighbor not in cost_so_far or new_cost < cost_so_far[neighbor]:
                cost_so_far[neighbor] = new_cost
                priority = new_cost + neighbor.distance_to(goal)
                sequence += 1
                heapq.heappush(frontier, (priority, sequence, neighbor))
                came_from[neighbor] = current

    if goal not in came_from:
        return []

    path = []
    current = goal
    while current != start:
        path.append(current)
        previous = came_from[current]
        if previous is None:
            break
        current = previous
    path.reverse()
    return path


def path_energy(path: Iterable[Point], energy_per_step: float) -> float:
    """Возвращает энергию, нужную для пути."""
    return len(list(path)) * energy_per_step

import unittest

from storage_agents.bus import MessageBus
from storage_agents.messages import (
    CHARGE_GRANTED,
    CHARGE_REQUESTED,
    TASK_COMPLETED,
    TASK_STARTED,
    ChargeGrant,
    ChargeRequest,
    Envelope,
    Point,
    TaskCompleted,
    TaskStarted,
)
from storage_agents.web_state import WebStateAgent
from storage_agents.world import WarehouseWorld


class WebStateTests(unittest.TestCase):
    def test_queued_charger_request_stays_visible(self) -> None:
        state = WebStateAgent(MessageBus(), WarehouseWorld.demo())

        state._apply(
            Envelope(
                sender="R1",
                recipient="ChargingStationAgent",
                topic=CHARGE_REQUESTED,
                payload=ChargeRequest(
                    robot_id="R1",
                    position=Point(2, 2),
                    battery=21.0,
                    reason="low battery",
                ),
            )
        )
        state._apply(
            Envelope(
                sender="ChargingStationAgent",
                recipient="R1",
                topic=CHARGE_GRANTED,
                payload=ChargeGrant(
                    robot_id="R1",
                    accepted=False,
                    station=None,
                    message="queued",
                ),
            )
        )

        self.assertEqual(state.snapshot()["charging"]["waiting"], ["R1"])

    def test_average_completion_seconds_uses_started_to_completed_duration(self) -> None:
        state = WebStateAgent(MessageBus(), WarehouseWorld.demo())

        state._orders["O001"] = {
            "id": "O001",
            "status": "in_progress",
            "startedAt": 10.0,
        }
        state._apply(
            Envelope(
                sender="R1",
                topic=TASK_COMPLETED,
                payload=TaskCompleted(
                    order_id="O001",
                    robot_id="R1",
                    battery_left=70.0,
                ),
                created_at=14.5,
            )
        )

        self.assertEqual(state.snapshot()["orders"]["avgCompletionSeconds"], 4.5)

    def test_started_event_marks_order_in_progress(self) -> None:
        state = WebStateAgent(MessageBus(), WarehouseWorld.demo())
        state._orders["O001"] = {"id": "O001", "status": "accepted"}

        state._apply(
            Envelope(
                sender="R1",
                topic=TASK_STARTED,
                payload=TaskStarted(order_id="O001", robot_id="R1"),
            )
        )

        order = state.snapshot()["orders"]["active"][0]
        self.assertEqual(order["status"], "in_progress")
        self.assertEqual(order["assignedRobot"], "R1")

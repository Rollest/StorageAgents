import asyncio
import unittest

from storage_agents.agents import ChargingStationAgent
from storage_agents.bus import MessageBus
from storage_agents.messages import (
    CHARGE_GRANTED,
    CHARGE_REQUESTED,
    ROBOT_STUCK,
    ChargeGrant,
    ChargeRequest,
    Envelope,
    Point,
    RobotStuck,
)


class ChargingStationTests(unittest.IsolatedAsyncioTestCase):
    async def test_station_grants_free_charger_to_robot(self) -> None:
        bus = MessageBus()
        robot_queue = await bus.subscribe("R1")
        station = ChargingStationAgent(bus=bus, stations=[Point(0, 0)])
        await station.start()

        try:
            await bus.publish(
                Envelope(
                    sender="R1",
                    recipient="ChargingStationAgent",
                    topic=CHARGE_REQUESTED,
                    payload=ChargeRequest(
                        robot_id="R1",
                        position=Point(2, 2),
                        battery=21.0,
                        reason="test",
                    ),
                )
            )

            message = await asyncio.wait_for(robot_queue.get(), timeout=0.1)
            self.assertEqual(message.topic, CHARGE_GRANTED)
            self.assertIsInstance(message.payload, ChargeGrant)
            self.assertTrue(message.payload.accepted)
            self.assertEqual(message.payload.station, Point(0, 0))
        finally:
            await station.stop()

    async def test_station_releases_charger_when_robot_gets_stuck(self) -> None:
        bus = MessageBus()
        station = ChargingStationAgent(bus=bus, stations=[Point(0, 0)])
        await station.start()

        try:
            station.occupied["R1"] = Point(0, 0)
            await bus.publish(
                Envelope(
                    sender="R1",
                    topic=ROBOT_STUCK,
                    payload=RobotStuck(
                        robot_id="R1",
                        position=Point(2, 2),
                        battery=0.0,
                        reason="test",
                    ),
                )
            )
            await asyncio.sleep(0)

            self.assertNotIn("R1", station.occupied)
        finally:
            await station.stop()

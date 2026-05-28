import asyncio
import unittest

from storage_agents.bus import MessageBus
from storage_agents.messages import Envelope


class MessageBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_message_goes_only_to_recipient(self) -> None:
        bus = MessageBus()
        sender = await bus.subscribe("sender")
        recipient = await bus.subscribe("recipient")
        observer = await bus.subscribe("observer")

        await bus.publish(
            Envelope(
                sender="sender",
                recipient="recipient",
                topic="demo",
                payload={"ok": True},
            )
        )

        message = await asyncio.wait_for(recipient.get(), timeout=0.1)
        self.assertEqual(message.payload, {"ok": True})
        self.assertTrue(sender.empty())
        self.assertTrue(observer.empty())

    async def test_broadcast_skips_sender(self) -> None:
        bus = MessageBus()
        sender = await bus.subscribe("sender")
        observer = await bus.subscribe("observer")

        await bus.publish(Envelope(sender="sender", topic="demo", payload=1))

        message = await asyncio.wait_for(observer.get(), timeout=0.1)
        self.assertEqual(message.payload, 1)
        self.assertTrue(sender.empty())

    async def test_observer_receives_direct_messages_without_deciding_anything(self) -> None:
        bus = MessageBus()
        recipient = await bus.subscribe("recipient")
        observer = await bus.subscribe("observer", observe_all=True)

        await bus.publish(
            Envelope(
                sender="sender",
                recipient="recipient",
                topic="direct",
                payload=42,
            )
        )

        direct_message = await asyncio.wait_for(recipient.get(), timeout=0.1)
        observed_message = await asyncio.wait_for(observer.get(), timeout=0.1)
        self.assertEqual(direct_message.payload, 42)
        self.assertEqual(observed_message.payload, 42)

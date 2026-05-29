import asyncio
from collections import deque
from typing import Deque, Dict, List, Optional, Set

from .messages import Envelope


class MessageBus:
    """Routes messages without making scheduling decisions."""

    def __init__(self, history_size: int = 80) -> None:
        """Initializes the instance."""
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._observers: Set[str] = set()
        self._lock: Optional[asyncio.Lock] = None
        self.history: Deque[Envelope] = deque(maxlen=history_size)

    async def subscribe(self, agent_id: str, observe_all: bool = False) -> asyncio.Queue:
        """Subscribes an agent to the message bus."""
        async with self._get_lock():
            queue: asyncio.Queue = asyncio.Queue()
            self._subscribers[agent_id] = queue
            if observe_all:
                self._observers.add(agent_id)
            return queue

    async def unsubscribe(self, agent_id: str) -> None:
        """Removes an agent subscription."""
        async with self._get_lock():
            self._subscribers.pop(agent_id, None)
            self._observers.discard(agent_id)

    async def publish(self, envelope: Envelope) -> None:
        """Publishes an envelope to matching subscribers."""
        async with self._get_lock():
            if envelope.recipient:
                target_ids = {envelope.recipient}
            else:
                target_ids = {
                    agent_id
                    for agent_id in self._subscribers
                    if agent_id != envelope.sender
                }
            target_ids.update(agent_id for agent_id in self._observers if agent_id != envelope.sender)
            targets: List[asyncio.Queue] = [
                self._subscribers[agent_id]
                for agent_id in target_ids
                if agent_id in self._subscribers
            ]
            self.history.append(envelope)

        for queue in targets:
            await queue.put(envelope)

    def _get_lock(self) -> asyncio.Lock:
        """Returns the lock."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

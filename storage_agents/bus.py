import asyncio
from collections import deque
from typing import Deque, Dict, List, Optional, Set

from .messages import Envelope


class MessageBus:
    """Transport-only message bus.

    The bus knows where queues are, but it does not inspect payloads or make
    scheduling decisions. That keeps the decision logic inside agents.
    """

    def __init__(self, history_size: int = 80) -> None:
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._observers: Set[str] = set()
        self._lock: Optional[asyncio.Lock] = None
        self.history: Deque[Envelope] = deque(maxlen=history_size)

    async def subscribe(self, agent_id: str, observe_all: bool = False) -> asyncio.Queue:
        async with self._get_lock():
            queue: asyncio.Queue = asyncio.Queue()
            self._subscribers[agent_id] = queue
            if observe_all:
                self._observers.add(agent_id)
            return queue

    async def unsubscribe(self, agent_id: str) -> None:
        async with self._get_lock():
            self._subscribers.pop(agent_id, None)
            self._observers.discard(agent_id)

    async def publish(self, envelope: Envelope) -> None:
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
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

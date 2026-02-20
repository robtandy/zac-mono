from __future__ import annotations

import asyncio
import logging
from typing import Any

from websockets.asyncio.server import ServerConnection

from agent import AgentClient
from agent.events import EventType

from .protocol import ClientMessage, ProtocolError, context_info_message, error_message, serialize_event, user_message

logger = logging.getLogger(__name__)


class Session:
    """Binds connected WebSocket clients to a single agent instance.

    Serializes prompts (pi handles one at a time) and broadcasts
    agent events to all connected clients.
    """

    def __init__(self, agent: AgentClient) -> None:
        self.agent = agent
        self.clients: set[ServerConnection] = set()
        self._prompt_lock = asyncio.Lock()

    def add_client(self, ws: ServerConnection) -> None:
        self.clients.add(ws)
        logger.info("Client connected (%d total)", len(self.clients))

    def remove_client(self, ws: ServerConnection) -> None:
        self.clients.discard(ws)
        logger.info("Client disconnected (%d total)", len(self.clients))

    async def broadcast(self, message: str) -> None:
        logger.debug("Broadcast: %s", message)
        if not self.clients:
            return
        await asyncio.gather(
            *(ws.send(message) for ws in self.clients),
            return_exceptions=True,
        )

    async def handle_client_message(self, ws: ServerConnection, data: str) -> None:
        logger.debug("Client message: %s", data)
        try:
            msg = ClientMessage.from_json(data)
        except ProtocolError as e:
            await ws.send(error_message(str(e)))
            return

        match msg.type:
            case "prompt":
                await self._handle_prompt(msg.message)
            case "steer":
                logger.debug("Steer: %s", msg.message)
                await self.agent.steer(msg.message)
            case "abort":
                logger.debug("Abort requested")
                await self.agent.abort()
            case "context_request":
                data = self.agent.context_info()
                await ws.send(context_info_message(data))

    async def _handle_prompt(self, message: str) -> None:
        # Broadcast user message to all clients so they stay in sync
        await self.broadcast(user_message(message))
        async with self._prompt_lock:
            try:
                async for event in self.agent.prompt(message):
                    await self.broadcast(serialize_event(event))
            except Exception as e:
                logger.exception("Error during prompt handling")
                await self.broadcast(error_message(str(e)))

from __future__ import annotations

import asyncio
import logging
import signal

from websockets.asyncio.server import ServerConnection, serve

from agent import AgentClient

from .session import Session

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8765


async def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, debug: bool = False) -> None:
    """Start the WebSocket gateway server."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    agent = AgentClient()
    session = Session(agent)

    async def handler(ws: ServerConnection) -> None:
        session.add_client(ws)
        try:
            async for message in ws:
                await session.handle_client_message(ws, message)
        finally:
            session.remove_client(ws)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    async with serve(handler, host, port):
        logger.info("Gateway listening on ws://%s:%d", host, port)
        await agent.start()
        logger.info("Agent started")
        await shutdown.wait()

    logger.info("Shutting down...")
    await agent.stop()
    logger.info("Agent stopped")

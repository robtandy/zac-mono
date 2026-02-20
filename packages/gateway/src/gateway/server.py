from __future__ import annotations

import asyncio
import logging
import mimetypes
import signal
import ssl
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from agent import AgentClient

from .session import Session

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8765


def _make_http_handler(web_dir: Path):
    """Create an HTTP handler that serves static files from web_dir."""

    def process_request(connection: ServerConnection, request: Request) -> Response | None:
        # If this is a WebSocket upgrade, let it through
        if "websocket" in request.headers.get("Upgrade", "").lower():
            return None

        # Map URL path to file
        path = request.path
        if path == "/":
            path = "/index.html"

        file_path = web_dir / path.lstrip("/")

        # Security: prevent directory traversal
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(web_dir.resolve())):
                return Response(403, "Forbidden", Headers())
        except (ValueError, OSError):
            return Response(403, "Forbidden", Headers())

        if not file_path.is_file():
            return Response(404, "Not Found", Headers(), b"Not Found")

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body = file_path.read_bytes()
        headers = Headers({
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-cache",
        })
        return Response(200, "OK", headers, body)

    return process_request


async def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    debug: bool = False,
    web_dir: str | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    log_file: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
) -> None:
    """Start the WebSocket gateway server."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)

    agent = AgentClient(model=model, system_prompt=system_prompt)
    session = Session(agent)

    async def handler(ws: ServerConnection) -> None:
        session.add_client(ws)
        tasks: set[asyncio.Task] = set()
        try:
            async for message in ws:
                task = asyncio.create_task(
                    session.handle_client_message(ws, message)
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            for task in tasks:
                task.cancel()
            session.remove_client(ws)

    serve_kwargs: dict[str, Any] = {}
    if web_dir:
        web_path = Path(web_dir).resolve()
        if not web_path.is_dir():
            logger.error("Web directory not found: %s", web_dir)
            return
        serve_kwargs["process_request"] = _make_http_handler(web_path)
        logger.info("Serving web UI from %s", web_path)

    if tls_cert and tls_key:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(tls_cert, tls_key)
        serve_kwargs["ssl"] = ssl_ctx
        scheme = "wss"
        http_scheme = "https"
    else:
        scheme = "ws"
        http_scheme = "http"

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    async with serve(handler, host, port, **serve_kwargs):
        logger.info("Gateway listening on %s://%s:%d", scheme, host, port)
        if web_dir:
            logger.info("Web UI at %s://%s:%d", http_scheme, host, port)
        await agent.start()
        logger.info("Agent started")
        await shutdown.wait()

    logger.info("Shutting down...")
    await agent.stop()
    logger.info("Agent stopped")

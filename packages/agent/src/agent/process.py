from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from .exceptions import AgentError, ProcessNotRunning

logger = logging.getLogger(__name__)


class PiProcess:
    """Manages the pi subprocess lifecycle: start, stop, send commands, read events."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_lines: list[str] = []

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        self._stderr_lines = []
        try:
            self._process = await asyncio.create_subprocess_exec(
                "pi", "--mode", "rpc", "--no-session",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise AgentError("'pi' command not found. Install with: npm install -g @mariozechner/pi-coding-agent")
        except OSError as e:
            raise AgentError(f"Failed to start pi process: {e}")

        # Start background task to drain stderr
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # Give the process a moment to fail on startup
        await asyncio.sleep(0.1)
        if not self.running:
            stderr = self.get_stderr()
            code = self._process.returncode
            self._process = None
            raise AgentError(
                f"pi process exited immediately (code {code})"
                + (f": {stderr}" if stderr else "")
            )

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            text = line.decode().rstrip()
            self._stderr_lines.append(text)
            logger.debug("pi stderr: %s", text)

    def get_stderr(self) -> str:
        """Return all collected stderr output."""
        return "\n".join(self._stderr_lines)

    async def stop(self) -> None:
        if self._process is None:
            return
        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        self._process = None

    async def send(self, data: dict[str, Any]) -> None:
        if not self.running:
            stderr = self.get_stderr()
            raise ProcessNotRunning(
                "pi process is not running"
                + (f": {stderr}" if stderr else "")
            )
        assert self._process is not None and self._process.stdin is not None
        line = json.dumps(data) + "\n"
        logger.debug("-> pi: %s", line.rstrip())
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def read_events(self) -> AsyncIterator[dict[str, Any]]:
        if not self.running:
            stderr = self.get_stderr()
            raise ProcessNotRunning(
                "pi process is not running"
                + (f": {stderr}" if stderr else "")
            )
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                # stdout closed — process likely died
                if not self.running:
                    # Wait a moment for stderr to flush
                    await asyncio.sleep(0.05)
                    stderr = self.get_stderr()
                    code = self._process.returncode if self._process else None
                    raise AgentError(
                        f"pi process exited unexpectedly (code {code})"
                        + (f": {stderr}" if stderr else "")
                    )
                break
            try:
                event = json.loads(line)
                logger.debug("<- pi: %s", line.rstrip().decode())
                yield event
            except json.JSONDecodeError:
                logger.debug("<- pi (non-JSON): %s", line.rstrip().decode())
                continue

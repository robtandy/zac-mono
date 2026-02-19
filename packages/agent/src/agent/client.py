from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .events import AgentEvent, EventType
from .exceptions import AgentError
from .process import PiProcess

logger = logging.getLogger(__name__)


def _translate_event(raw: dict[str, Any]) -> AgentEvent | None:
    """Translate a raw pi RPC event into an AgentEvent, or None if ignored."""
    event_type = raw.get("type")

    match event_type:
        case "turn_start":
            return AgentEvent(type=EventType.TURN_START)

        case "message_update":
            ame = raw.get("assistantMessageEvent", {})
            if ame.get("type") == "text_delta":
                return AgentEvent(type=EventType.TEXT_DELTA, delta=ame.get("delta", ""))
            return None

        case "tool_execution_start":
            return AgentEvent(
                type=EventType.TOOL_START,
                tool_name=raw.get("toolName", ""),
                tool_call_id=raw.get("toolCallId", ""),
                args=raw.get("args", {}),
            )

        case "tool_execution_update":
            partial = raw.get("partialResult", {})
            content = partial.get("content", [])
            text = "".join(item.get("text", "") for item in content)
            return AgentEvent(
                type=EventType.TOOL_UPDATE,
                tool_call_id=raw.get("toolCallId", ""),
                tool_name=raw.get("toolName", ""),
                partial_result=text,
            )

        case "tool_execution_end":
            result = raw.get("result", {})
            content = result.get("content", [])
            text = "".join(item.get("text", "") for item in content)
            return AgentEvent(
                type=EventType.TOOL_END,
                tool_call_id=raw.get("toolCallId", ""),
                tool_name=raw.get("toolName", ""),
                result=text,
                is_error=raw.get("isError", False),
            )

        case "turn_end":
            return AgentEvent(type=EventType.TURN_END)

        case "agent_end":
            return AgentEvent(type=EventType.AGENT_END)

        case "extension_error":
            return AgentEvent(
                type=EventType.ERROR,
                message=raw.get("message", "Unknown error"),
            )

        case _:
            # Ignored: message_start, message_end, agent_start,
            # auto_compaction_*, auto_retry_*, response
            return None


class AgentClient:
    """High-level async API for interacting with pi via RPC."""

    def __init__(self) -> None:
        self._process = PiProcess()

    @property
    def running(self) -> bool:
        return self._process.running

    async def start(self) -> None:
        await self._process.start()
        logger.info("pi process started (pid %s)", self._process._process.pid if self._process._process else "?")

    async def stop(self) -> None:
        await self._process.stop()

    async def prompt(self, message: str) -> AsyncIterator[AgentEvent]:
        try:
            await self._process.send({"type": "prompt", "message": message})
        except AgentError as e:
            logger.error("Failed to send prompt: %s", e)
            yield AgentEvent(type=EventType.ERROR, message=str(e))
            return

        try:
            async for raw in self._process.read_events():
                event = _translate_event(raw)
                if event is not None:
                    yield event
                if raw.get("type") == "agent_end":
                    break
        except AgentError as e:
            logger.error("pi process error: %s", e)
            yield AgentEvent(type=EventType.ERROR, message=str(e))

    async def steer(self, message: str) -> None:
        await self._process.send({"type": "steer", "message": message})

    async def abort(self) -> None:
        await self._process.send({"type": "abort"})

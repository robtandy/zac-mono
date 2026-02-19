"""Tests for AgentClient with mocked PiProcess."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.client import AgentClient
from agent.events import EventType


def _make_events(*raw_events):
    """Create an async iterator from a list of raw event dicts."""
    async def _iter():
        for event in raw_events:
            yield event
    return _iter()


@pytest.fixture
def client():
    return AgentClient()


class TestAgentClientPrompt:
    @pytest.mark.asyncio
    async def test_prompt_yields_translated_events(self, client):
        raw_events = [
            {"type": "turn_start"},
            {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Hello"}},
            {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": " world"}},
            {"type": "turn_end"},
            {"type": "agent_end"},
        ]
        with patch.object(client._process, "send", new_callable=AsyncMock) as mock_send:
            with patch.object(client._process, "read_events", return_value=_make_events(*raw_events)):
                events = []
                async for event in client.prompt("Hello"):
                    events.append(event)

        mock_send.assert_called_once_with({"type": "prompt", "message": "Hello"})
        assert len(events) == 5
        assert events[0].type == EventType.TURN_START
        assert events[1].type == EventType.TEXT_DELTA
        assert events[1].delta == "Hello"
        assert events[2].type == EventType.TEXT_DELTA
        assert events[2].delta == " world"
        assert events[3].type == EventType.TURN_END
        assert events[4].type == EventType.AGENT_END

    @pytest.mark.asyncio
    async def test_prompt_skips_ignored_events(self, client):
        raw_events = [
            {"type": "message_start"},
            {"type": "turn_start"},
            {"type": "response", "command": "prompt", "success": True},
            {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Hi"}},
            {"type": "message_end"},
            {"type": "agent_end"},
        ]
        with patch.object(client._process, "send", new_callable=AsyncMock):
            with patch.object(client._process, "read_events", return_value=_make_events(*raw_events)):
                events = []
                async for event in client.prompt("test"):
                    events.append(event)

        types = [e.type for e in events]
        assert EventType.TURN_START in types
        assert EventType.TEXT_DELTA in types
        assert EventType.AGENT_END in types
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_prompt_with_tools(self, client):
        raw_events = [
            {"type": "turn_start"},
            {"type": "tool_execution_start", "toolName": "bash", "toolCallId": "tc_1", "args": {"command": "ls"}},
            {"type": "tool_execution_update", "toolCallId": "tc_1", "toolName": "bash", "partialResult": {"content": [{"text": "file.txt"}]}},
            {"type": "tool_execution_end", "toolCallId": "tc_1", "toolName": "bash", "result": {"content": [{"text": "file.txt\n"}]}, "isError": False},
            {"type": "turn_end"},
            {"type": "agent_end"},
        ]
        with patch.object(client._process, "send", new_callable=AsyncMock):
            with patch.object(client._process, "read_events", return_value=_make_events(*raw_events)):
                events = []
                async for event in client.prompt("ls"):
                    events.append(event)

        assert events[1].type == EventType.TOOL_START
        assert events[1].tool_name == "bash"
        assert events[1].args == {"command": "ls"}
        assert events[2].type == EventType.TOOL_UPDATE
        assert events[2].partial_result == "file.txt"
        assert events[3].type == EventType.TOOL_END
        assert events[3].result == "file.txt\n"
        assert events[3].is_error is False


class TestAgentClientCommands:
    @pytest.mark.asyncio
    async def test_steer(self, client):
        with patch.object(client._process, "send", new_callable=AsyncMock) as mock_send:
            await client.steer("do something else")
        mock_send.assert_called_once_with({"type": "steer", "message": "do something else"})

    @pytest.mark.asyncio
    async def test_abort(self, client):
        with patch.object(client._process, "send", new_callable=AsyncMock) as mock_send:
            await client.abort()
        mock_send.assert_called_once_with({"type": "abort"})

    @pytest.mark.asyncio
    async def test_start_stop(self, client):
        with patch.object(client._process, "start", new_callable=AsyncMock) as mock_start:
            await client.start()
        mock_start.assert_called_once()

        with patch.object(client._process, "stop", new_callable=AsyncMock) as mock_stop:
            await client.stop()
        mock_stop.assert_called_once()

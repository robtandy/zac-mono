"""Session tests with mocked agent."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.events import AgentEvent, EventType
from gateway.session import Session


def _mock_agent():
    agent = MagicMock()
    agent.steer = AsyncMock()
    agent.abort = AsyncMock()
    return agent


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    return ws


def _make_prompt_events(*events):
    async def prompt(message):
        for event in events:
            yield event
    return prompt


class TestSessionClientManagement:
    def test_add_remove_client(self):
        agent = _mock_agent()
        session = Session(agent)
        ws = _mock_ws()

        session.add_client(ws)
        assert ws in session.clients

        session.remove_client(ws)
        assert ws not in session.clients

    def test_remove_nonexistent_client(self):
        agent = _mock_agent()
        session = Session(agent)
        ws = _mock_ws()
        # Should not raise
        session.remove_client(ws)


class TestSessionBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(self):
        agent = _mock_agent()
        session = Session(agent)
        ws1 = _mock_ws()
        ws2 = _mock_ws()
        session.add_client(ws1)
        session.add_client(ws2)

        await session.broadcast('{"type": "turn_start"}')
        ws1.send.assert_called_once_with('{"type": "turn_start"}')
        ws2.send.assert_called_once_with('{"type": "turn_start"}')

    @pytest.mark.asyncio
    async def test_broadcast_no_clients(self):
        agent = _mock_agent()
        session = Session(agent)
        # Should not raise
        await session.broadcast('{"type": "turn_start"}')


class TestSessionHandleMessage:
    @pytest.mark.asyncio
    async def test_handle_prompt(self):
        agent = _mock_agent()
        events = [
            AgentEvent(type=EventType.TURN_START),
            AgentEvent(type=EventType.TEXT_DELTA, delta="Hi"),
            AgentEvent(type=EventType.AGENT_END),
        ]
        agent.prompt = _make_prompt_events(*events)
        session = Session(agent)
        ws = _mock_ws()
        session.add_client(ws)

        await session.handle_client_message(ws, '{"type": "prompt", "message": "Hello"}')

        assert ws.send.call_count == 4
        calls = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        assert calls[0]["type"] == "user_message"
        assert calls[0]["message"] == "Hello"
        assert calls[1]["type"] == "turn_start"
        assert calls[2]["type"] == "text_delta"
        assert calls[2]["delta"] == "Hi"
        assert calls[3]["type"] == "agent_end"

    @pytest.mark.asyncio
    async def test_handle_steer(self):
        agent = _mock_agent()
        session = Session(agent)
        ws = _mock_ws()
        session.add_client(ws)

        await session.handle_client_message(ws, '{"type": "steer", "message": "do X"}')
        agent.steer.assert_called_once_with("do X")

    @pytest.mark.asyncio
    async def test_handle_abort(self):
        agent = _mock_agent()
        session = Session(agent)
        ws = _mock_ws()
        session.add_client(ws)

        await session.handle_client_message(ws, '{"type": "abort"}')
        agent.abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_invalid_message(self):
        agent = _mock_agent()
        session = Session(agent)
        ws = _mock_ws()
        session.add_client(ws)

        await session.handle_client_message(ws, "not json")
        ws.send.assert_called_once()
        error = json.loads(ws.send.call_args[0][0])
        assert error["type"] == "error"

    @pytest.mark.asyncio
    async def test_handle_context_request(self):
        agent = _mock_agent()
        agent.context_info = MagicMock(return_value={
            "system": 10,
            "tools": 20,
            "user": 30,
            "assistant": 40,
            "tool_results": 5,
            "context_window": 128000,
        })
        session = Session(agent)
        ws1 = _mock_ws()
        ws2 = _mock_ws()
        session.add_client(ws1)
        session.add_client(ws2)

        await session.handle_client_message(ws1, '{"type": "context_request"}')

        # Only the requesting client should receive the response
        agent.context_info.assert_called_once()
        ws1.send.assert_called_once()
        ws2.send.assert_not_called()

        response = json.loads(ws1.send.call_args[0][0])
        assert response["type"] == "context_info"
        assert response["system"] == 10
        assert response["context_window"] == 128000

    @pytest.mark.asyncio
    async def test_prompt_serialized(self):
        """Verify that concurrent prompts are serialized."""
        agent = _mock_agent()
        call_order = []

        async def slow_prompt(message):
            call_order.append(f"start:{message}")
            await asyncio.sleep(0.05)
            yield AgentEvent(type=EventType.TEXT_DELTA, delta=message)
            call_order.append(f"end:{message}")
            yield AgentEvent(type=EventType.AGENT_END)

        agent.prompt = slow_prompt
        session = Session(agent)
        ws = _mock_ws()
        session.add_client(ws)

        await asyncio.gather(
            session.handle_client_message(ws, '{"type": "prompt", "message": "first"}'),
            session.handle_client_message(ws, '{"type": "prompt", "message": "second"}'),
        )

        # Prompts should not interleave
        assert call_order[0] == "start:first"
        assert call_order[1] == "end:first"
        assert call_order[2] == "start:second"
        assert call_order[3] == "end:second"

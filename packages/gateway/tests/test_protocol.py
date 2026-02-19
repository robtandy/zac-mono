"""Unit tests for gateway protocol: message parsing and event serialization."""

import json

import pytest

from agent.events import AgentEvent, EventType
from gateway.protocol import ClientMessage, ProtocolError, error_message, serialize_event


class TestClientMessageParsing:
    def test_parse_prompt(self):
        msg = ClientMessage.from_json('{"type": "prompt", "message": "Hello"}')
        assert msg.type == "prompt"
        assert msg.message == "Hello"

    def test_parse_steer(self):
        msg = ClientMessage.from_json('{"type": "steer", "message": "Do this instead"}')
        assert msg.type == "steer"
        assert msg.message == "Do this instead"

    def test_parse_abort(self):
        msg = ClientMessage.from_json('{"type": "abort"}')
        assert msg.type == "abort"
        assert msg.message == ""

    def test_invalid_json(self):
        with pytest.raises(ProtocolError, match="Invalid JSON"):
            ClientMessage.from_json("not json")

    def test_not_object(self):
        with pytest.raises(ProtocolError, match="must be a JSON object"):
            ClientMessage.from_json('"just a string"')

    def test_unknown_type(self):
        with pytest.raises(ProtocolError, match="Unknown message type"):
            ClientMessage.from_json('{"type": "unknown"}')

    def test_missing_type(self):
        with pytest.raises(ProtocolError, match="Unknown message type"):
            ClientMessage.from_json('{"message": "hello"}')

    def test_prompt_missing_message(self):
        with pytest.raises(ProtocolError, match="requires a 'message' field"):
            ClientMessage.from_json('{"type": "prompt"}')

    def test_steer_missing_message(self):
        with pytest.raises(ProtocolError, match="requires a 'message' field"):
            ClientMessage.from_json('{"type": "steer"}')

    def test_prompt_empty_message(self):
        with pytest.raises(ProtocolError, match="requires a 'message' field"):
            ClientMessage.from_json('{"type": "prompt", "message": ""}')


class TestSerializeEvent:
    def test_serialize_text_delta(self):
        event = AgentEvent(type=EventType.TEXT_DELTA, delta="hello")
        result = json.loads(serialize_event(event))
        assert result == {"type": "text_delta", "delta": "hello"}

    def test_serialize_turn_start(self):
        event = AgentEvent(type=EventType.TURN_START)
        result = json.loads(serialize_event(event))
        assert result == {"type": "turn_start"}

    def test_serialize_tool_start(self):
        event = AgentEvent(
            type=EventType.TOOL_START,
            tool_name="bash",
            tool_call_id="tc_1",
            args={"cmd": "ls"},
        )
        result = json.loads(serialize_event(event))
        assert result["type"] == "tool_start"
        assert result["tool_name"] == "bash"

    def test_serialize_error(self):
        event = AgentEvent(type=EventType.ERROR, message="oops")
        result = json.loads(serialize_event(event))
        assert result == {"type": "error", "message": "oops"}


class TestErrorMessage:
    def test_error_message(self):
        result = json.loads(error_message("something broke"))
        assert result == {"type": "error", "message": "something broke"}

"""Unit tests for _translate_event (pure function, no subprocess needed)."""

from agent.client import _translate_event
from agent.events import AgentEvent, EventType


class TestTranslateEvent:
    def test_turn_start(self):
        event = _translate_event({"type": "turn_start"})
        assert event == AgentEvent(type=EventType.TURN_START)

    def test_text_delta(self):
        raw = {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "Hello"},
        }
        event = _translate_event(raw)
        assert event is not None
        assert event.type == EventType.TEXT_DELTA
        assert event.delta == "Hello"

    def test_message_update_non_text_ignored(self):
        raw = {
            "type": "message_update",
            "assistantMessageEvent": {"type": "thinking_delta", "delta": "..."},
        }
        assert _translate_event(raw) is None

    def test_message_update_empty_assistant_event(self):
        raw = {"type": "message_update"}
        assert _translate_event(raw) is None

    def test_tool_execution_start(self):
        raw = {
            "type": "tool_execution_start",
            "toolName": "bash",
            "toolCallId": "tc_123",
            "args": {"command": "ls"},
        }
        event = _translate_event(raw)
        assert event is not None
        assert event.type == EventType.TOOL_START
        assert event.tool_name == "bash"
        assert event.tool_call_id == "tc_123"
        assert event.args == {"command": "ls"}

    def test_tool_execution_update(self):
        raw = {
            "type": "tool_execution_update",
            "toolCallId": "tc_123",
            "toolName": "bash",
            "partialResult": {"content": [{"text": "file1.txt\n"}]},
        }
        event = _translate_event(raw)
        assert event is not None
        assert event.type == EventType.TOOL_UPDATE
        assert event.tool_call_id == "tc_123"
        assert event.tool_name == "bash"
        assert event.partial_result == "file1.txt\n"

    def test_tool_execution_end(self):
        raw = {
            "type": "tool_execution_end",
            "toolCallId": "tc_123",
            "toolName": "bash",
            "result": {"content": [{"text": "file1.txt\nfile2.txt\n"}]},
            "isError": False,
        }
        event = _translate_event(raw)
        assert event is not None
        assert event.type == EventType.TOOL_END
        assert event.tool_call_id == "tc_123"
        assert event.result == "file1.txt\nfile2.txt\n"
        assert event.is_error is False

    def test_tool_execution_end_with_error(self):
        raw = {
            "type": "tool_execution_end",
            "toolCallId": "tc_456",
            "toolName": "bash",
            "result": {"content": [{"text": "command not found"}]},
            "isError": True,
        }
        event = _translate_event(raw)
        assert event is not None
        assert event.is_error is True

    def test_tool_execution_end_multiple_content(self):
        raw = {
            "type": "tool_execution_end",
            "toolCallId": "tc_789",
            "toolName": "read",
            "result": {"content": [{"text": "line1\n"}, {"text": "line2\n"}]},
            "isError": False,
        }
        event = _translate_event(raw)
        assert event is not None
        assert event.result == "line1\nline2\n"

    def test_turn_end(self):
        event = _translate_event({"type": "turn_end"})
        assert event == AgentEvent(type=EventType.TURN_END)

    def test_agent_end(self):
        event = _translate_event({"type": "agent_end"})
        assert event == AgentEvent(type=EventType.AGENT_END)

    def test_extension_error(self):
        raw = {"type": "extension_error", "message": "Something broke"}
        event = _translate_event(raw)
        assert event is not None
        assert event.type == EventType.ERROR
        assert event.message == "Something broke"

    def test_ignored_events(self):
        for event_type in [
            "message_start",
            "message_end",
            "agent_start",
            "auto_compaction_start",
            "auto_compaction_end",
            "auto_retry_start",
            "auto_retry_end",
            "response",
        ]:
            assert _translate_event({"type": event_type}) is None

    def test_unknown_event_ignored(self):
        assert _translate_event({"type": "some_future_event"}) is None


class TestAgentEventToDict:
    def test_turn_start(self):
        event = AgentEvent(type=EventType.TURN_START)
        assert event.to_dict() == {"type": "turn_start"}

    def test_text_delta(self):
        event = AgentEvent(type=EventType.TEXT_DELTA, delta="hi")
        assert event.to_dict() == {"type": "text_delta", "delta": "hi"}

    def test_tool_start(self):
        event = AgentEvent(
            type=EventType.TOOL_START,
            tool_name="bash",
            tool_call_id="tc_1",
            args={"cmd": "ls"},
        )
        d = event.to_dict()
        assert d["type"] == "tool_start"
        assert d["tool_name"] == "bash"
        assert d["tool_call_id"] == "tc_1"
        assert d["args"] == {"cmd": "ls"}

    def test_tool_update(self):
        event = AgentEvent(
            type=EventType.TOOL_UPDATE,
            tool_call_id="tc_1",
            tool_name="bash",
            partial_result="output",
        )
        d = event.to_dict()
        assert d["type"] == "tool_update"
        assert d["partial_result"] == "output"

    def test_tool_end(self):
        event = AgentEvent(
            type=EventType.TOOL_END,
            tool_call_id="tc_1",
            tool_name="bash",
            result="done",
            is_error=False,
        )
        d = event.to_dict()
        assert d["type"] == "tool_end"
        assert d["result"] == "done"
        assert d["is_error"] is False

    def test_turn_end(self):
        assert AgentEvent(type=EventType.TURN_END).to_dict() == {"type": "turn_end"}

    def test_agent_end(self):
        assert AgentEvent(type=EventType.AGENT_END).to_dict() == {"type": "agent_end"}

    def test_error(self):
        event = AgentEvent(type=EventType.ERROR, message="fail")
        assert event.to_dict() == {"type": "error", "message": "fail"}

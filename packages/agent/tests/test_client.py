"""Tests for AgentClient with mocked OpenAI SDK."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.client import AgentClient
from agent.events import EventType
from agent.exceptions import AgentError, AgentNotRunning
from agent.tools import ToolRegistry, ToolResult, default_tools


def _make_text_chunk(content, finish_reason=None):
    """Create a mock streaming chunk with text content."""
    delta = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _make_tool_call_chunk(index, tool_call_id=None, name=None, arguments=None, finish_reason=None):
    """Create a mock streaming chunk with a tool call delta."""
    tc_delta = SimpleNamespace(
        index=index,
        id=tool_call_id,
        function=SimpleNamespace(name=name, arguments=arguments) if (name or arguments) else None,
    )
    delta = SimpleNamespace(content=None, tool_calls=[tc_delta])
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _make_finish_chunk(finish_reason):
    """Create a mock streaming chunk with only a finish reason."""
    delta = SimpleNamespace(content=None, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class MockStream:
    """Mock async iterator for streaming responses, with a close method."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def close(self):
        pass


@pytest.fixture
def client():
    """Create an AgentClient with mocked environment."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        c = AgentClient(model="test-model", system_prompt="test prompt")
    return c


async def _start_client(client):
    """Start a client with mocked OpenAI constructor."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        with patch("agent.client.AsyncOpenAI") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            await client.start()
            return mock_instance


async def _collect_events(client, message="test"):
    events = []
    async for event in client.prompt(message):
        events.append(event)
    return events


class TestContextInfo:
    def test_context_info_structure(self, client):
        info = client.context_info()
        assert set(info.keys()) == {"system", "tools", "user", "assistant", "tool_results", "context_window"}
        for key in info:
            assert isinstance(info[key], int)

    def test_context_info_system_prompt_estimate(self, client):
        info = client.context_info()
        expected = len("test prompt") // 4
        assert info["system"] == expected

    def test_context_info_default_context_window(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            c = AgentClient(model="unknown/model", system_prompt="hi")
        info = c.context_info()
        assert info["context_window"] == 128000

    def test_context_info_known_model_context_window(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            c = AgentClient(model="anthropic/claude-sonnet-4", system_prompt="hi")
        info = c.context_info()
        assert info["context_window"] == 200000

    def test_context_info_with_messages(self, client):
        client._messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "tool", "tool_call_id": "tc1", "content": "result"},
        ]
        info = client.context_info()
        assert info["user"] > 0
        assert info["assistant"] > 0
        assert info["tool_results"] > 0

    def test_context_info_empty_conversation(self, client):
        info = client.context_info()
        assert info["user"] == 0
        assert info["assistant"] == 0
        assert info["tool_results"] == 0
        assert info["system"] > 0
        assert info["tools"] >= 0


class TestAgentClientLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running(self, client):
        mock_openai = await _start_client(client)
        assert client.running is True

    @pytest.mark.asyncio
    async def test_start_without_api_key(self):
        c = AgentClient()
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENROUTER_API_KEY", None)
            with pytest.raises(AgentError, match="OPENROUTER_API_KEY"):
                await c.start()

    @pytest.mark.asyncio
    async def test_stop(self, client):
        mock_openai = await _start_client(client)
        await client.stop()
        assert client.running is False

    @pytest.mark.asyncio
    async def test_prompt_when_not_running(self):
        c = AgentClient()
        with pytest.raises(AgentNotRunning):
            async for _ in c.prompt("hello"):
                pass


class TestAgentClientPrompt:
    @pytest.mark.asyncio
    async def test_simple_text_response(self, client):
        mock_openai = await _start_client(client)

        chunks = [
            _make_text_chunk("Hello"),
            _make_text_chunk(" world"),
            _make_finish_chunk("stop"),
        ]
        mock_openai.chat.completions.create = AsyncMock(return_value=MockStream(chunks))

        events = await _collect_events(client, "Hi")

        types = [e.type for e in events]
        assert types == [
            EventType.TURN_START,
            EventType.TEXT_DELTA,
            EventType.TEXT_DELTA,
            EventType.TURN_END,
            EventType.AGENT_END,
        ]
        assert events[1].delta == "Hello"
        assert events[2].delta == " world"

    @pytest.mark.asyncio
    async def test_tool_call_and_followup(self, client):
        mock_openai = await _start_client(client)

        # First response: tool call
        tool_chunks = [
            _make_tool_call_chunk(0, tool_call_id="tc_1", name="bash"),
            _make_tool_call_chunk(0, arguments='{"command":'),
            _make_tool_call_chunk(0, arguments='"ls"}'),
            _make_finish_chunk("tool_calls"),
        ]
        # Second response: text after tool execution
        text_chunks = [
            _make_text_chunk("Done!"),
            _make_finish_chunk("stop"),
        ]
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=[MockStream(tool_chunks), MockStream(text_chunks)]
        )

        # Mock the bash tool's execute method
        bash_tool = client._tools.get("bash")
        bash_tool.execute = AsyncMock(return_value=ToolResult(output="file.txt"))

        events = await _collect_events(client, "list files")

        types = [e.type for e in events]
        assert types == [
            EventType.TURN_START,
            EventType.TOOL_START,
            EventType.TOOL_END,
            EventType.TURN_END,
            EventType.TURN_START,
            EventType.TEXT_DELTA,
            EventType.TURN_END,
            EventType.AGENT_END,
        ]
        # Verify tool events
        tool_start = events[1]
        assert tool_start.tool_name == "bash"
        assert tool_start.args == {"command": "ls"}
        tool_end = events[2]
        assert tool_end.result == "file.txt"
        assert tool_end.is_error is False

    @pytest.mark.asyncio
    async def test_multi_tool_call(self, client):
        mock_openai = await _start_client(client)

        # Response with two tool calls
        chunks = [
            _make_tool_call_chunk(0, tool_call_id="tc_1", name="bash"),
            _make_tool_call_chunk(0, arguments='{"command": "ls"}'),
            _make_tool_call_chunk(1, tool_call_id="tc_2", name="read"),
            _make_tool_call_chunk(1, arguments='{"file_path": "f.txt"}'),
            _make_finish_chunk("tool_calls"),
        ]
        text_chunks = [
            _make_text_chunk("All done"),
            _make_finish_chunk("stop"),
        ]
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=[MockStream(chunks), MockStream(text_chunks)]
        )

        client._tools.get("bash").execute = AsyncMock(return_value=ToolResult(output="files"))
        client._tools.get("read").execute = AsyncMock(return_value=ToolResult(output="content"))

        events = await _collect_events(client, "do stuff")

        tool_starts = [e for e in events if e.type == EventType.TOOL_START]
        tool_ends = [e for e in events if e.type == EventType.TOOL_END]
        assert len(tool_starts) == 2
        assert len(tool_ends) == 2
        assert tool_starts[0].tool_name == "bash"
        assert tool_starts[1].tool_name == "read"

    @pytest.mark.asyncio
    async def test_unknown_tool(self, client):
        mock_openai = await _start_client(client)

        chunks = [
            _make_tool_call_chunk(0, tool_call_id="tc_1", name="nonexistent"),
            _make_tool_call_chunk(0, arguments='{}'),
            _make_finish_chunk("tool_calls"),
        ]
        text_chunks = [
            _make_text_chunk("ok"),
            _make_finish_chunk("stop"),
        ]
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=[MockStream(chunks), MockStream(text_chunks)]
        )

        events = await _collect_events(client, "use unknown tool")

        tool_end = [e for e in events if e.type == EventType.TOOL_END][0]
        assert tool_end.is_error is True
        assert "Unknown tool" in tool_end.result

    @pytest.mark.asyncio
    async def test_conversation_memory(self, client):
        mock_openai = await _start_client(client)

        chunks1 = [_make_text_chunk("Hi"), _make_finish_chunk("stop")]
        chunks2 = [_make_text_chunk("Bye"), _make_finish_chunk("stop")]
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=[MockStream(chunks1), MockStream(chunks2)]
        )

        await _collect_events(client, "first")
        await _collect_events(client, "second")

        # Check that messages accumulate
        assert len(client._messages) == 4  # user, assistant, user, assistant
        assert client._messages[0] == {"role": "user", "content": "first"}
        assert client._messages[1] == {"role": "assistant", "content": "Hi"}
        assert client._messages[2] == {"role": "user", "content": "second"}
        assert client._messages[3] == {"role": "assistant", "content": "Bye"}


class TestAgentClientAbort:
    @pytest.mark.asyncio
    async def test_abort_mid_stream(self, client):
        mock_openai = await _start_client(client)

        chunks = [
            _make_text_chunk("Hello"),
            _make_text_chunk(" world"),
            _make_text_chunk(" more"),
            _make_finish_chunk("stop"),
        ]

        # Create a stream that triggers abort after first chunk
        class AbortingStream(MockStream):
            def __init__(self, chunks, client):
                super().__init__(chunks)
                self._client = client
                self._count = 0

            async def __anext__(self):
                result = await super().__anext__()
                self._count += 1
                if self._count == 2:
                    self._client._abort_event.set()
                return result

        mock_openai.chat.completions.create = AsyncMock(
            return_value=AbortingStream(chunks, client)
        )

        events = await _collect_events(client, "hello")
        types = [e.type for e in events]

        # Should have TURN_START, some TEXT_DELTAs, then TURN_END + AGENT_END
        assert EventType.TURN_START in types
        assert EventType.TURN_END in types
        assert EventType.AGENT_END in types


class TestAgentClientSteer:
    @pytest.mark.asyncio
    async def test_steer_injected_between_turns(self, client):
        mock_openai = await _start_client(client)

        # First call: tool call
        tool_chunks = [
            _make_tool_call_chunk(0, tool_call_id="tc_1", name="bash"),
            _make_tool_call_chunk(0, arguments='{"command": "ls"}'),
            _make_finish_chunk("tool_calls"),
        ]
        # Second call (after tool execution + steer): text
        text_chunks = [
            _make_text_chunk("Steered!"),
            _make_finish_chunk("stop"),
        ]

        call_count = 0
        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockStream(tool_chunks)
            return MockStream(text_chunks)

        mock_openai.chat.completions.create = mock_create

        client._tools.get("bash").execute = AsyncMock(return_value=ToolResult(output="done"))

        # Inject steer before the tool loop's second iteration
        await client.steer("change direction")

        events = await _collect_events(client, "start")

        # The steer message should be in the conversation
        user_msgs = [m for m in client._messages if m["role"] == "user"]
        assert len(user_msgs) == 2
        assert user_msgs[1]["content"] == "change direction"


class TestAgentClientRetry:
    @pytest.mark.asyncio
    async def test_retry_on_rate_limit(self, client):
        from openai import APIStatusError
        mock_openai = await _start_client(client)

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {}

        call_count = 0
        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise APIStatusError(
                    message="Rate limited",
                    response=error_response,
                    body=None,
                )
            return MockStream([_make_text_chunk("ok"), _make_finish_chunk("stop")])

        mock_openai.chat.completions.create = mock_create

        with patch("agent.client.asyncio.sleep", new_callable=AsyncMock):
            events = await _collect_events(client, "hello")

        assert call_count == 2
        text_events = [e for e in events if e.type == EventType.TEXT_DELTA]
        assert len(text_events) == 1
        assert text_events[0].delta == "ok"

    @pytest.mark.asyncio
    async def test_no_retry_on_client_error(self, client):
        from openai import APIStatusError
        mock_openai = await _start_client(client)

        error_response = MagicMock()
        error_response.status_code = 401
        error_response.headers = {}

        async def mock_create(**kwargs):
            raise APIStatusError(
                message="Unauthorized",
                response=error_response,
                body=None,
            )

        mock_openai.chat.completions.create = mock_create

        events = await _collect_events(client, "hello")

        types = [e.type for e in events]
        assert EventType.ERROR in types
        error_event = [e for e in events if e.type == EventType.ERROR][0]
        assert "401" in error_event.message

    @pytest.mark.asyncio
    async def test_retry_exhaustion(self, client):
        from openai import APIStatusError
        mock_openai = await _start_client(client)

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.headers = {}

        async def mock_create(**kwargs):
            raise APIStatusError(
                message="Server error",
                response=error_response,
                body=None,
            )

        mock_openai.chat.completions.create = mock_create

        with patch("agent.client.asyncio.sleep", new_callable=AsyncMock):
            events = await _collect_events(client, "hello")

        types = [e.type for e in events]
        assert EventType.ERROR in types
        assert EventType.AGENT_END in types
        error_event = [e for e in events if e.type == EventType.ERROR][0]
        assert "Max retries" in error_event.message


def _tool_call_stream(calls, finish_reason="tool_calls"):
    """Build a MockStream that returns one or more tool calls.

    calls: list of (id, name, args_dict) tuples
    """
    chunks = []
    for idx, (call_id, name, args_dict) in enumerate(calls):
        chunks.append(_make_tool_call_chunk(idx, tool_call_id=call_id, name=name))
        chunks.append(_make_tool_call_chunk(idx, arguments=json.dumps(args_dict)))
    chunks.append(_make_finish_chunk(finish_reason))
    return MockStream(chunks)


def _text_stream(text, finish_reason="stop"):
    """Build a MockStream that returns a simple text response."""
    return MockStream([_make_text_chunk(text), _make_finish_chunk(finish_reason)])


class TestToolUseIntegration:
    """Integration tests: mock the OpenAI API but use real tools against a temp dir."""

    @pytest.fixture
    def client_with_tools(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            c = AgentClient(model="test-model", system_prompt="test")
        return c

    async def _start(self, client):
        return await _start_client(client)

    @pytest.mark.asyncio
    async def test_bash_tool_executes_real_command(self, client_with_tools):
        client = client_with_tools
        mock_openai = await self._start(client)

        mock_openai.chat.completions.create = AsyncMock(side_effect=[
            _tool_call_stream([("tc_1", "bash", {"command": "echo integration-test-output"})]),
            _text_stream("Done"),
        ])

        events = await _collect_events(client, "run echo")

        tool_end = [e for e in events if e.type == EventType.TOOL_END][0]
        assert tool_end.is_error is False
        assert "integration-test-output" in tool_end.result

        # Tool result should be in conversation memory
        tool_msgs = [m for m in client._messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "integration-test-output" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_write_then_read_real_filesystem(self, client_with_tools, tmp_path):
        client = client_with_tools
        mock_openai = await self._start(client)
        target = str(tmp_path / "hello.txt")

        # Turn 1: write a file. Turn 2: read it back. Turn 3: text summary.
        mock_openai.chat.completions.create = AsyncMock(side_effect=[
            _tool_call_stream([("tc_1", "write", {"file_path": target, "content": "hello from test"})]),
            _tool_call_stream([("tc_2", "read", {"file_path": target})]),
            _text_stream("File written and verified"),
        ])

        events = await _collect_events(client, "write and read")

        tool_ends = [e for e in events if e.type == EventType.TOOL_END]
        assert len(tool_ends) == 2

        # Write succeeded
        assert tool_ends[0].tool_name == "write"
        assert tool_ends[0].is_error is False

        # Read returned the written content with line numbers
        assert tool_ends[1].tool_name == "read"
        assert tool_ends[1].is_error is False
        assert "hello from test" in tool_ends[1].result

        # Verify the actual file on disk
        assert Path(target).read_text() == "hello from test"

    @pytest.mark.asyncio
    async def test_edit_tool_modifies_file(self, client_with_tools, tmp_path):
        client = client_with_tools
        mock_openai = await self._start(client)
        target = tmp_path / "code.py"
        target.write_text("def greet():\n    return 'hello'\n")

        mock_openai.chat.completions.create = AsyncMock(side_effect=[
            _tool_call_stream([("tc_1", "edit", {
                "file_path": str(target),
                "old_text": "return 'hello'",
                "new_text": "return 'goodbye'",
            })]),
            _text_stream("Updated"),
        ])

        events = await _collect_events(client, "change greeting")

        tool_end = [e for e in events if e.type == EventType.TOOL_END][0]
        assert tool_end.is_error is False

        assert target.read_text() == "def greet():\n    return 'goodbye'\n"

    @pytest.mark.asyncio
    async def test_bash_error_propagated_to_conversation(self, client_with_tools):
        client = client_with_tools
        mock_openai = await self._start(client)

        mock_openai.chat.completions.create = AsyncMock(side_effect=[
            _tool_call_stream([("tc_1", "bash", {"command": "false"})]),
            _text_stream("Command failed"),
        ])

        events = await _collect_events(client, "run failing command")

        tool_end = [e for e in events if e.type == EventType.TOOL_END][0]
        assert tool_end.is_error is True

        # The error result should be in conversation memory as a tool message
        tool_msgs = [m for m in client._messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "Exit code: 1" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_read_missing_file_error_sent_to_model(self, client_with_tools):
        client = client_with_tools
        mock_openai = await self._start(client)

        mock_openai.chat.completions.create = AsyncMock(side_effect=[
            _tool_call_stream([("tc_1", "read", {"file_path": "/no/such/file.txt"})]),
            _text_stream("File not found, sorry"),
        ])

        events = await _collect_events(client, "read missing file")

        tool_end = [e for e in events if e.type == EventType.TOOL_END][0]
        assert tool_end.is_error is True
        assert "not found" in tool_end.result.lower()

        # Model should have received the error as a tool result
        tool_msgs = [m for m in client._messages if m["role"] == "tool"]
        assert "not found" in tool_msgs[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_multi_tool_real_execution(self, client_with_tools, tmp_path):
        """Two tools in a single API response both execute against real filesystem."""
        client = client_with_tools
        mock_openai = await self._start(client)

        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"

        mock_openai.chat.completions.create = AsyncMock(side_effect=[
            _tool_call_stream([
                ("tc_1", "write", {"file_path": str(f1), "content": "aaa"}),
                ("tc_2", "write", {"file_path": str(f2), "content": "bbb"}),
            ]),
            _text_stream("Both files written"),
        ])

        events = await _collect_events(client, "write two files")

        tool_ends = [e for e in events if e.type == EventType.TOOL_END]
        assert len(tool_ends) == 2
        assert all(e.is_error is False for e in tool_ends)

        assert f1.read_text() == "aaa"
        assert f2.read_text() == "bbb"

    @pytest.mark.asyncio
    async def test_tool_result_included_in_followup_api_call(self, client_with_tools):
        """Verify the API is called with tool results in the messages."""
        client = client_with_tools
        mock_openai = await self._start(client)

        api_calls = []

        async def capture_create(**kwargs):
            api_calls.append(kwargs)
            if len(api_calls) == 1:
                return _tool_call_stream([("tc_1", "bash", {"command": "echo captured"})])
            return _text_stream("ok")

        mock_openai.chat.completions.create = capture_create

        await _collect_events(client, "test capture")

        # Second API call should include the tool result message
        assert len(api_calls) == 2
        second_call_messages = api_calls[1]["messages"]
        tool_result_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        assert len(tool_result_msgs) == 1
        assert tool_result_msgs[0]["tool_call_id"] == "tc_1"
        assert "captured" in tool_result_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_edit_ambiguous_match_error(self, client_with_tools, tmp_path):
        """Edit tool returns error for ambiguous matches, model gets the error."""
        client = client_with_tools
        mock_openai = await self._start(client)

        target = tmp_path / "dup.txt"
        target.write_text("foo\nfoo\n")

        mock_openai.chat.completions.create = AsyncMock(side_effect=[
            _tool_call_stream([("tc_1", "edit", {
                "file_path": str(target),
                "old_text": "foo",
                "new_text": "bar",
            })]),
            _text_stream("Edit failed due to ambiguous match"),
        ])

        events = await _collect_events(client, "edit dup")

        tool_end = [e for e in events if e.type == EventType.TOOL_END][0]
        assert tool_end.is_error is True
        assert "2 times" in tool_end.result

        # File should be unchanged
        assert target.read_text() == "foo\nfoo\n"

    @pytest.mark.asyncio
    async def test_abort_before_tool_execution(self, client_with_tools, tmp_path):
        """Abort during streaming prevents tool from executing."""
        client = client_with_tools
        mock_openai = await self._start(client)
        target = tmp_path / "should_not_exist.txt"

        tool_chunks = [
            _make_tool_call_chunk(0, tool_call_id="tc_1", name="write"),
            _make_tool_call_chunk(0, arguments=json.dumps(
                {"file_path": str(target), "content": "nope"}
            )),
            _make_finish_chunk("tool_calls"),
        ]

        class AbortAfterStream(MockStream):
            def __init__(self, chunks, cli):
                super().__init__(chunks)
                self._cli = cli
                self._count = 0

            async def __anext__(self):
                result = await super().__anext__()
                self._count += 1
                if self._count == 2:
                    self._cli._abort_event.set()
                return result

        mock_openai.chat.completions.create = AsyncMock(
            return_value=AbortAfterStream(tool_chunks, client)
        )

        events = await _collect_events(client, "write a file")

        # Should have been aborted — no tool execution
        assert not target.exists()
        assert EventType.AGENT_END in [e.type for e in events]

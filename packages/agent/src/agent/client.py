from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIConnectionError, APIStatusError

from .events import AgentEvent, EventType
from .exceptions import AgentError, AgentNotRunning
from .tools import ToolRegistry, default_tools

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic/claude-sonnet-4"
_DEFAULT_SYSTEM_PROMPT = "You are a helpful coding assistant. Use the provided tools to help the user with their tasks."

_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503}

_MODEL_CONTEXT_SIZES: dict[str, int] = {
    "anthropic/claude-sonnet-4": 200000,
    "mistralai/mistral-large-2512": 128000,
}

_COMPACTION_THRESHOLD = 0.8
_KEEP_RECENT_TOKENS = 20000
_CHARS_PER_TOKEN = 4


def _load_system_prompt() -> str:
    path = os.environ.get("ZAC_SYSTEM_PROMPT_FILE")
    if path:
        try:
            with open(path) as f:
                content = f.read().strip()
            if content:
                logger.info("Loaded system prompt from %s", path)
                return content
        except OSError as e:
            logger.warning("Failed to read ZAC_SYSTEM_PROMPT_FILE=%s: %s", path, e)
    return _DEFAULT_SYSTEM_PROMPT


class AgentClient:
    """Async agent that streams LLM responses via OpenRouter and executes tools."""

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._model = model or _DEFAULT_MODEL
        self._system_prompt = system_prompt or _load_system_prompt()
        self._tools = tools or default_tools()
        self._client: AsyncOpenAI | None = None
        self._messages: list[dict[str, Any]] = []
        self._abort_event = asyncio.Event()
        self._steer_queue: asyncio.Queue[str] = asyncio.Queue()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise AgentError("OPENROUTER_API_KEY environment variable is not set")
        self._client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            max_retries=0,
        )
        self._messages = []
        self._abort_event.clear()
        self._running = True
        logger.info("Agent started (model=%s)", self._model)

    async def stop(self) -> None:
        self._abort_event.set()
        if self._client:
            await self._client.close()
            self._client = None
        self._running = False
        logger.info("Agent stopped")

    async def prompt(self, message: str) -> AsyncIterator[AgentEvent]:
        if not self._running or self._client is None:
            raise AgentNotRunning("Agent is not running. Call start() first.")

        self._abort_event.clear()
        self._messages.append({"role": "user", "content": message})

        yield AgentEvent(type=EventType.TURN_START)

        while True:
            # Drain steer queue
            while not self._steer_queue.empty():
                try:
                    steer_msg = self._steer_queue.get_nowait()
                    self._messages.append({"role": "user", "content": steer_msg})
                except asyncio.QueueEmpty:
                    break

            if self._abort_event.is_set():
                yield AgentEvent(type=EventType.TURN_END)
                yield AgentEvent(type=EventType.AGENT_END)
                return

            # Auto-compact if approaching context limit
            if self._should_compact():
                yield AgentEvent(type=EventType.COMPACTION_START)
                try:
                    summary, tokens_before = await self._compact()
                    yield AgentEvent(
                        type=EventType.COMPACTION_END,
                        summary=summary,
                        tokens_before=tokens_before,
                    )
                except Exception as e:
                    logger.warning("Compaction failed: %s", e)
                    yield AgentEvent(
                        type=EventType.COMPACTION_END,
                        summary="",
                        tokens_before=0,
                        message=f"Compaction failed: {e}",
                    )

            # Call API with retry
            try:
                stream = await self._create_stream_with_retry()
            except AgentError as e:
                yield AgentEvent(type=EventType.ERROR, message=str(e))
                yield AgentEvent(type=EventType.AGENT_END)
                return

            # Stream response
            content_parts: list[str] = []
            tool_calls_by_index: dict[int, dict[str, Any]] = {}
            finish_reason = None

            try:
                async for chunk in stream:
                    if self._abort_event.is_set():
                        await stream.close()
                        yield AgentEvent(type=EventType.TURN_END)
                        yield AgentEvent(type=EventType.AGENT_END)
                        return

                    choice = chunk.choices[0] if chunk.choices else None
                    if choice is None:
                        continue

                    delta = choice.delta
                    if delta.content:
                        content_parts.append(delta.content)
                        yield AgentEvent(type=EventType.TEXT_DELTA, delta=delta.content)

                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_by_index:
                                tool_calls_by_index[idx] = {
                                    "id": tc_delta.id or "",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tc = tool_calls_by_index[idx]
                            if tc_delta.id:
                                tc["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tc["function"]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tc["function"]["arguments"] += tc_delta.function.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
            except (APIConnectionError, APIStatusError) as e:
                yield AgentEvent(type=EventType.ERROR, message=f"Stream error: {e}")
                yield AgentEvent(type=EventType.AGENT_END)
                return

            # Build assistant message
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            content_text = "".join(content_parts)
            if content_text:
                assistant_msg["content"] = content_text

            sorted_tool_calls = [
                tool_calls_by_index[i] for i in sorted(tool_calls_by_index)
            ]
            if sorted_tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    }
                    for tc in sorted_tool_calls
                ]

            self._messages.append(assistant_msg)

            # If no tool calls, we're done
            if not sorted_tool_calls or finish_reason != "tool_calls":
                break

            # Execute tools
            for tc in sorted_tool_calls:
                if self._abort_event.is_set():
                    yield AgentEvent(type=EventType.TURN_END)
                    yield AgentEvent(type=EventType.AGENT_END)
                    return

                func_name = tc["function"]["name"]
                call_id = tc["id"]
                try:
                    func_args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    func_args = {}

                yield AgentEvent(
                    type=EventType.TOOL_START,
                    tool_name=func_name,
                    tool_call_id=call_id,
                    args=func_args,
                )

                tool = self._tools.get(func_name)
                if tool is None:
                    result_text = f"Unknown tool: {func_name}"
                    is_error = True
                else:
                    try:
                        result = await tool.execute(func_args)
                        result_text = result.output
                        is_error = result.is_error
                    except Exception as e:
                        result_text = f"Tool execution error: {e}"
                        is_error = True

                yield AgentEvent(
                    type=EventType.TOOL_END,
                    tool_name=func_name,
                    tool_call_id=call_id,
                    result=result_text,
                    is_error=is_error,
                )

                self._messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result_text,
                })

            # Continue the loop for next turn
            yield AgentEvent(type=EventType.TURN_END)
            yield AgentEvent(type=EventType.TURN_START)

        yield AgentEvent(type=EventType.TURN_END)
        yield AgentEvent(type=EventType.AGENT_END)

    async def steer(self, message: str) -> AsyncIterator[AgentEvent]:
        """Steer the agent with a message or command (e.g., /compact)."""
        if message.strip() == "/compact":
            if not self._running or self._client is None:
                raise AgentNotRunning("Agent is not running. Call start() first.")
            yield AgentEvent(type=EventType.COMPACTION_START)
            try:
                summary, tokens_before = await self._compact()
                yield AgentEvent(
                    type=EventType.COMPACTION_END,
                    summary=summary,
                    tokens_before=tokens_before,
                )
            except Exception as e:
                logger.warning("Compaction failed: %s", e)
                yield AgentEvent(
                    type=EventType.COMPACTION_END,
                    summary="",
                    tokens_before=0,
                    message=f"Compaction failed: {e}",
                )
        else:
            self._steer_queue.put_nowait(message)

    def context_info(self) -> dict[str, int]:
        """Return token estimates per category and total context window size."""
        system_chars = len(self._system_prompt)
        tools_chars = len(json.dumps(self._tools.schemas()))

        user_chars = 0
        assistant_chars = 0
        tool_chars = 0
        for msg in self._messages:
            serialized = len(json.dumps(msg))
            match msg.get("role"):
                case "user":
                    user_chars += serialized
                case "assistant":
                    assistant_chars += serialized
                case "tool":
                    tool_chars += serialized

        return {
            "system": system_chars // 4,
            "tools": tools_chars // 4,
            "user": user_chars // 4,
            "assistant": assistant_chars // 4,
            "tool_results": tool_chars // 4,
            "context_window": _MODEL_CONTEXT_SIZES.get(self._model, 128000),
        }

    def _estimate_tokens(self) -> int:
        total = len(self._system_prompt) + len(json.dumps(self._tools.schemas()))
        for msg in self._messages:
            total += len(json.dumps(msg))
        return total // _CHARS_PER_TOKEN

    def _should_compact(self) -> bool:
        context_window = _MODEL_CONTEXT_SIZES.get(self._model, 128000)
        return self._estimate_tokens() > int(context_window * _COMPACTION_THRESHOLD)

    def _find_cut_point(self) -> int:
        """Return index of first message to keep. Returns 0 if nothing to cut."""
        accumulated = 0
        cut_index = 0
        for i in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[i]
            accumulated += len(json.dumps(msg)) // _CHARS_PER_TOKEN
            if accumulated >= _KEEP_RECENT_TOKENS:
                for j in range(i, len(self._messages)):
                    if self._messages[j]["role"] in ("user", "assistant"):
                        cut_index = j
                        break
                break
        return cut_index

    async def _compact(self) -> tuple[str, int]:
        """Summarize old messages and replace them. Returns (summary, tokens_before)."""
        tokens_before = self._estimate_tokens()
        cut = self._find_cut_point()
        if cut <= 0:
            return "(Nothing to compact — context is small enough)", tokens_before

        old_messages = self._messages[:cut]
        kept_messages = self._messages[cut:]

        summary_prompt = [
            {"role": "system", "content": (
                "Summarize the following conversation history. "
                "Cover: the user's goal, progress made, key decisions, "
                "files read/modified, and what the next steps were. "
                "Be concise but preserve all important context."
            )},
            *old_messages,
            {"role": "user", "content": "Summarize the conversation so far."},
        ]

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=summary_prompt,
            stream=False,
        )
        summary = response.choices[0].message.content or "No summary generated."

        self._messages = [
            {"role": "user", "content": f"[Previous conversation summary]\n{summary}"},
            {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I help?"},
            *kept_messages,
        ]

        return summary, tokens_before

    async def abort(self) -> None:
        self._abort_event.set()

    async def _create_stream_with_retry(self):
        """Create a streaming completion with exponential backoff retry."""
        backoff = _INITIAL_BACKOFF
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                return await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": self._system_prompt},
                        *self._messages,
                    ],
                    tools=self._tools.schemas() or None,
                    stream=True,
                )
            except APIStatusError as e:
                last_error = e
                if e.status_code not in _RETRYABLE_STATUS_CODES:
                    raise AgentError(f"API error ({e.status_code}): {e.message}")
                logger.warning(
                    "Retryable API error (attempt %d/%d, status %d): %s",
                    attempt + 1, _MAX_RETRIES, e.status_code, e.message,
                )
            except APIConnectionError as e:
                last_error = e
                logger.warning(
                    "Connection error (attempt %d/%d): %s",
                    attempt + 1, _MAX_RETRIES, e,
                )

            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

        raise AgentError(f"Max retries ({_MAX_RETRIES}) exceeded: {last_error}")

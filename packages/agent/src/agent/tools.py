from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    output: str
    is_error: bool = False


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Tool(ABC):
    @abstractmethod
    def definition(self) -> ToolDefinition:
        ...

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        defn = tool.definition()
        self._tools[defn.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.definition().to_openai_schema() for t in self._tools.values()]


_MAX_OUTPUT = 30_000
_BASH_TIMEOUT = 120


class BashTool(Tool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description="Execute a bash command and return stdout+stderr.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute.",
                    },
                },
                "required": ["command"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        command = args.get("command", "")
        if not command:
            return ToolResult(output="No command provided.", is_error=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_BASH_TIMEOUT
            )
            output = stdout.decode(errors="replace")
            if len(output) > _MAX_OUTPUT:
                output = output[:_MAX_OUTPUT] + "\n... (output truncated)"
            if proc.returncode != 0:
                output = f"Exit code: {proc.returncode}\n{output}"
            return ToolResult(output=output, is_error=proc.returncode != 0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                output=f"Command timed out after {_BASH_TIMEOUT}s.",
                is_error=True,
            )
        except OSError as e:
            return ToolResult(output=f"Failed to execute command: {e}", is_error=True)


class ReadTool(Tool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description="Read a file and return its contents with line numbers.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-based).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read.",
                    },
                },
                "required": ["file_path"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        file_path = args.get("file_path", "")
        offset = args.get("offset", 1)
        limit = args.get("limit")

        if not file_path:
            return ToolResult(output="No file_path provided.", is_error=True)
        try:
            path = Path(file_path)
            text = path.read_text()
        except FileNotFoundError:
            return ToolResult(output=f"File not found: {file_path}", is_error=True)
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        lines = text.splitlines(keepends=True)
        start = max(0, offset - 1)
        if limit is not None:
            lines = lines[start:start + limit]
        else:
            lines = lines[start:]

        numbered = []
        for i, line in enumerate(lines, start=start + 1):
            numbered.append(f"{i}\t{line.rstrip()}")
        return ToolResult(output="\n".join(numbered))


class WriteTool(Tool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write",
            description="Write content to a file. Creates parent directories if needed.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        file_path = args.get("file_path", "")
        content = args.get("content", "")

        if not file_path:
            return ToolResult(output="No file_path provided.", is_error=True)
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return ToolResult(output=f"Wrote {len(content)} bytes to {file_path}")
        except OSError as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)


class EditTool(Tool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit",
            description="Find and replace text in a file. old_text must match exactly once.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to edit.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "The exact text to find.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                },
                "required": ["file_path", "old_text", "new_text"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        file_path = args.get("file_path", "")
        old_text = args.get("old_text", "")
        new_text = args.get("new_text", "")

        if not file_path:
            return ToolResult(output="No file_path provided.", is_error=True)
        if not old_text:
            return ToolResult(output="No old_text provided.", is_error=True)

        try:
            path = Path(file_path)
            content = path.read_text()
        except FileNotFoundError:
            return ToolResult(output=f"File not found: {file_path}", is_error=True)
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        count = content.count(old_text)
        if count == 0:
            return ToolResult(output="old_text not found in file.", is_error=True)
        if count > 1:
            return ToolResult(
                output=f"old_text matches {count} times. Must match exactly once.",
                is_error=True,
            )

        new_content = content.replace(old_text, new_text, 1)
        try:
            path.write_text(new_content)
        except OSError as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)
        return ToolResult(output="Edit applied successfully.")


class SearchWebTool(Tool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_web",
            description="Search the web using DuckDuckGo (no API key required).",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    }
                },
                "required": ["query"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(output="No query provided.", is_error=True)

        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_redirect=1"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                
                results = []
                if data.get("AbstractText"):
                    results.append(f"**Summary**: {data["AbstractText"]}")
                if data.get("Answer"):
                    results.append(f"**Answer**: {data["Answer"]}")
                if data.get("RelatedTopics"):
                    for topic in data["RelatedTopics"][:3]:  # Limit to 3 topics
                        if "Text" in topic:
                            results.append(f"- {topic["Text"]}")
                        elif "Topics" in topic:
                            for subtopic in topic["Topics"][:2]:  # Limit to 2 subtopics
                                results.append(f"- {subtopic["Text"]}")
                
                if not results:
                    return ToolResult(output="No results found.")
                
                return ToolResult(output="\n".join(results))
        except Exception as e:
            return ToolResult(output=f"Failed to search: {e}", is_error=True)


def default_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(BashTool())
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(EditTool())
    registry.register(SearchWebTool())
    return registry

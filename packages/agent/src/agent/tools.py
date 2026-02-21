# Hashline-based read/edit tools
# 
# This implementation is inspired by the "harness problem" and the hashline editing pattern
# described in https://blog.can.ac/2026/02/12/the-harness-problem/

import asyncio
import json
import os
import re
import zlib
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


def _hash_line(content: str) -> str:
    """Generate a short hash for a line of content."""
    # Use CRC32 for speed, take 2 hex chars (can also use 3)
    return format(zlib.crc32(content.encode()) & 0xFFFF, 'x')[:2]


def _parse_hashline(line: str) -> tuple[int, str, str] | None:
    """Parse a hashline in format 'line:hash|content'."""
    # Match pattern: number:hash|content
    match = re.match(r'^(\d+):([0-9a-f]+)\|(.*)$', line)
    if match:
        return int(match.group(1)), match.group(2), match.group(3)
    return None


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
            description="Read one or more files and return their contents with line numbers and content hashes. Use the hash to identify lines for editing.",
            parameters={
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "description": "Absolute path to a file to read.",
                        },
                        "description": "List of absolute paths to the files to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-based).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read per file.",
                    },
                },
                "required": ["file_paths"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        file_paths = args.get("file_paths", [])
        offset = args.get("offset", 1)
        limit = args.get("limit")

        if not file_paths:
            return ToolResult(output="No file_paths provided.", is_error=True)

        results = {}
        for file_path in file_paths:
            if not file_path:
                results[file_path] = "Error: Empty file path provided."
                continue

            try:
                path = Path(file_path)
                text = path.read_text()
            except FileNotFoundError:
                results[file_path] = f"Error: File not found: {file_path}"
                continue
            except OSError as e:
                results[file_path] = f"Error reading file: {e}"
                continue

            lines = text.splitlines(keepends=True)
            start = max(0, offset - 1)
            if limit is not None:
                lines = lines[start:start + limit]
            else:
                lines = lines[start:]

            # Output format: line:hash|content
            numbered = []
            for i, line in enumerate(lines, start=start + 1):
                # Remove trailing whitespace for hashing but keep the actual content
                content = line.rstrip('\n\r')
                hash_val = _hash_line(content)
                numbered.append(f"{i}:{hash_val}|{content}")
            results[file_path] = "\n".join(numbered)

        # Convert results to a structured JSON string for clarity
        return ToolResult(output=json.dumps(results, indent=2))


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
            description="Find and replace text in a file using content hashes. Use the hash from read output to identify lines.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to edit.",
                    },
                    "hash": {
                        "type": "string",
                        "description": "Line reference in format 'line:hash' (e.g., '42:ab') or range 'start:end' (e.g., '10:ab-15:cd'). Use the hash from read output.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                },
                "required": ["file_path", "hash", "new_text"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        file_path = args.get("file_path", "")
        hash_ref = args.get("hash", "")
        new_text = args.get("new_text", "")

        if not file_path:
            return ToolResult(output="No file_path provided.", is_error=True)
        if not hash_ref:
            return ToolResult(output="No hash provided. Use format 'line:hash' (e.g., '42:ab') or 'start:end' range.", is_error=True)
        if not new_text:
            return ToolResult(output="No new_text provided.", is_error=True)

        try:
            path = Path(file_path)
            content = path.read_text()
        except FileNotFoundError:
            return ToolResult(output=f"File not found: {file_path}", is_error=True)
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        lines = content.splitlines(keepends=True)
        
        # Track the hash -> (line_num, content) mapping
        hash_map: dict[tuple[int, str], int] = {}  # (line_num, hash) -> index in lines
        for i, line in enumerate(lines):
            content_stripped = line.rstrip('\n\r')
            if content_stripped:  # Only hash non-empty lines
                h = _hash_line(content_stripped)
                hash_map[(i + 1, h)] = i  # 1-based line number

        # Hash-based editing
        if hash_ref:
            # Parse hash reference: could be "line:hash" or "start:end" range
            if '-' in hash_ref and ':' in hash_ref:
                # Range format: "start_hash-end_hash" or "start_line:end_line"
                parts = hash_ref.split('-')
                if len(parts) != 2:
                    return ToolResult(output="Invalid hash range format. Use 'line:hash' or 'start:end'.", is_error=True)
                
                # Check if it's line:hash-line:hash format
                if ':' in parts[0] and ':' in parts[1]:
                    # Format: "line1:hash1-line2:hash2"
                    start_match = re.match(r'^(\d+):([0-9a-f]+)$', parts[0])
                    end_match = re.match(r'^(\d+):([0-9a-f]+)$', parts[1])
                    if not start_match or not end_match:
                        return ToolResult(output="Invalid hash range format.", is_error=True)
                    
                    start_line = int(start_match.group(1))
                    start_hash = start_match.group(2)
                    end_line = int(end_match.group(1))
                    end_hash = end_match.group(2)
                    
                    # Find the matching range
                    start_idx = None
                    end_idx = None
                    for i, line in enumerate(lines):
                        content_stripped = line.rstrip('\n\r')
                        if content_stripped:
                            h = _hash_line(content_stripped)
                            line_num = i + 1
                            if start_idx is None and line_num == start_line and h == start_hash:
                                start_idx = i
                            if start_idx is not None and line_num == end_line and h == end_hash:
                                end_idx = i
                                break
                    
                    if start_idx is None:
                        return ToolResult(output=f"Start hash {parts[0]} not found in file.", is_error=True)
                    if end_idx is None:
                        return ToolResult(output=f"End hash {parts[1]} not found in file.", is_error=True)
                    if end_idx < start_idx:
                        return ToolResult(output="End hash appears before start hash.", is_error=True)
                    
                    # Replace the range
                    # Preserve the original newlines
                    new_lines = lines[:start_idx] + [new_text + '\n' if not new_text.endswith('\n') and start_idx < len(lines) and lines[start_idx].endswith('\n') else new_text] + lines[end_idx + 1:]
                    new_content = ''.join(new_lines)
                else:
                    return ToolResult(output="Invalid range format. Use 'line:hash-line:hash'.", is_error=True)
            elif ':' in hash_ref:
                # Single line format: "line:hash"
                match = re.match(r'^(\d+):([0-9a-f]+)$', hash_ref)
                if not match:
                    return ToolResult(output="Invalid hash format. Use 'line:hash'.", is_error=True)
                
                line_num = int(match.group(1))
                target_hash = match.group(2)
                
                # Find the line with matching hash
                idx = None
                for i, line in enumerate(lines):
                    content_stripped = line.rstrip('\n\r')
                    if content_stripped:
                        h = _hash_line(content_stripped)
                        if i + 1 == line_num and h == target_hash:
                            idx = i
                            break
                
                if idx is None:
                    return ToolResult(output=f"Hash {hash_ref} not found in file. File may have changed since read.", is_error=True)
                
                # Replace the line
                new_lines = lines[:idx] + [new_text + ('\n' if lines[idx].endswith('\n') else '')] + lines[idx + 1:]
                new_content = ''.join(new_lines)
            else:
                return ToolResult(output="Invalid hash format. Use 'line:hash' or 'start:end'.", is_error=True)

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

        # Note: no_redirect=1 suppresses abstract text and related topics, so we don't use it
        url = f"https://api.duckduckgo.com/?q={query}&format=json"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

                results = []
                # Check both Abstract and AbstractText (API returns either depending on query)
                abstract = data.get("Abstract") or data.get("AbstractText")
                if abstract:
                    results.append(f"**Summary**: {abstract}")
                if data.get("Answer"):
                    results.append(f"**Answer**: {data['Answer']}")
                if data.get("RelatedTopics"):
                    for topic in data["RelatedTopics"][:3]:  # Limit to 3 topics
                        # API returns "Result" key, not "Text"
                        if "Result" in topic:
                            # Strip HTML tags from result
                            text = topic["Result"]
                            text = re.sub(r"<[^>]+>", "", text)
                            results.append(f"- {text}")
                        elif "Topics" in topic:
                            for subtopic in topic["Topics"][:2]:  # Limit to 2 subtopics
                                if "Result" in subtopic:
                                    text = subtopic["Result"]
                                    text = re.sub(r"<[^>]+>", "", text)
                                    results.append(f"- {text}")

                if not results:
                    return ToolResult(output="No results found.")

                return ToolResult(output="\n".join(results))
        except Exception as e:
            return ToolResult(output=f"Failed to search: {e}", is_error=True)


def default_tools() -> ToolRegistry:
    from .canvas_tool import CanvasTool

    registry = ToolRegistry()
    registry.register(BashTool())
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(EditTool())
    registry.register(SearchWebTool())
    registry.register(CanvasTool())
    return registry

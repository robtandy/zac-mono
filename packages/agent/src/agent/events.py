from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(Enum):
    TURN_START = "turn_start"
    TEXT_DELTA = "text_delta"
    TOOL_START = "tool_start"
    TOOL_UPDATE = "tool_update"
    TOOL_END = "tool_end"
    TURN_END = "turn_end"
    AGENT_END = "agent_end"
    ERROR = "error"


@dataclass
class AgentEvent:
    type: EventType
    delta: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    partial_result: str = ""
    is_error: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        match self.type:
            case EventType.TURN_START:
                return {"type": "turn_start"}
            case EventType.TEXT_DELTA:
                return {"type": "text_delta", "delta": self.delta}
            case EventType.TOOL_START:
                return {
                    "type": "tool_start",
                    "tool_name": self.tool_name,
                    "tool_call_id": self.tool_call_id,
                    "args": self.args,
                }
            case EventType.TOOL_UPDATE:
                return {
                    "type": "tool_update",
                    "tool_call_id": self.tool_call_id,
                    "tool_name": self.tool_name,
                    "partial_result": self.partial_result,
                }
            case EventType.TOOL_END:
                return {
                    "type": "tool_end",
                    "tool_call_id": self.tool_call_id,
                    "tool_name": self.tool_name,
                    "result": self.result,
                    "is_error": self.is_error,
                }
            case EventType.TURN_END:
                return {"type": "turn_end"}
            case EventType.AGENT_END:
                return {"type": "agent_end"}
            case EventType.ERROR:
                return {"type": "error", "message": self.message}

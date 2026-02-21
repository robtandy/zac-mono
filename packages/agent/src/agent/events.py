from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(Enum):
    TURN_START = "turn_start"
    TEXT_DELTA = "text_delta"
    TOOL_START = "tool_start"
    TOOL_UPDATE = "tool_update"
    TOOL_END = "tool_end"
    TURN_END = "turn_end"
    AGENT_END = "agent_end"
    ERROR = "error"
    COMPACTION_START = "compaction_start"
    COMPACTION_END = "compaction_end"
    CANVAS_UPDATE = "canvas_update"
    CANVAS_SCREENSHOT = "canvas_screenshot"
    CANVAS_DISMISS = "canvas_dismiss"
    MODEL_INFO = "model_info"


class AgentEvent(BaseModel):
    type: EventType
    delta: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    partial_result: str = ""
    is_error: bool = False
    message: str = ""
    summary: str = ""
    tokens_before: int = 0
    html: str = ""
    url: str = ""
    image_data: str = ""
    model_info: dict[str, Any] = Field(default_factory=dict)

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
            case EventType.COMPACTION_START:
                return {"type": "compaction_start"}
            case EventType.COMPACTION_END:
                return {
                    "type": "compaction_end",
                    "summary": self.summary,
                    "tokens_before": self.tokens_before,
                }
            case EventType.CANVAS_UPDATE:
                d: dict[str, Any] = {"type": "canvas_update"}
                if self.html:
                    d["html"] = self.html
                if self.url:
                    d["url"] = self.url
                return d
            case EventType.CANVAS_SCREENSHOT:
                return {"type": "canvas_screenshot", "image_data": self.image_data}
            case EventType.CANVAS_DISMISS:
                return {"type": "canvas_dismiss"}

            case EventType.MODEL_INFO:
                return {"type": "model_info", "model_info": self.model_info}

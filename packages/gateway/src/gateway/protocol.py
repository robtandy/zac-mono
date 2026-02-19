from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.events import AgentEvent


class ProtocolError(Exception):
    """Raised when a client message is malformed."""


@dataclass
class ClientMessage:
    type: str
    message: str = ""

    @classmethod
    def from_json(cls, data: str) -> ClientMessage:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as e:
            raise ProtocolError(f"Invalid JSON: {e}")

        if not isinstance(parsed, dict):
            raise ProtocolError("Message must be a JSON object")

        msg_type = parsed.get("type")
        if msg_type not in ("prompt", "steer", "abort"):
            raise ProtocolError(f"Unknown message type: {msg_type}")

        message = parsed.get("message", "")
        if msg_type in ("prompt", "steer") and not message:
            raise ProtocolError(f"'{msg_type}' requires a 'message' field")

        return cls(type=msg_type, message=message)


def serialize_event(event: AgentEvent) -> str:
    return json.dumps(event.to_dict())


def error_message(message: str) -> str:
    return json.dumps({"type": "error", "message": message})

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection

from agent import AgentClient
from agent.events import EventType

from .protocol import ClientMessage, ProtocolError, context_info_message, error_message, serialize_event, user_message

logger = logging.getLogger(__name__)


def _find_web_dir() -> Path | None:
    """Find packages/web directory by walking up from this file."""
    here = Path(__file__).resolve().parent
    for ancestor in (here, *here.parents):
        candidate = ancestor / "packages" / "web"
        if candidate.is_dir() and (candidate / "package.json").is_file():
            return candidate
    return None


def _ensure_web_node_modules(web_dir: Path) -> None:
    """Run npm install if node_modules is missing in the web directory."""
    if not (web_dir / "node_modules").is_dir():
        logger.info("Installing web dependencies...")
        result = subprocess.run(
            ["npm", "install"],
            cwd=str(web_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"npm install failed: {result.stderr}")
        logger.info("Web dependencies installed.")


class Session:
    """Binds connected WebSocket clients to a single agent instance.

    Serializes prompts (pi handles one at a time) and broadcasts
    agent events to all connected clients.
    """

    def __init__(self, agent: AgentClient) -> None:
        self.agent = agent
        self.clients: set[ServerConnection] = set()
        self._prompt_lock = asyncio.Lock()
        self._model_cache: list[dict[str, str]] | None = None

    def add_client(self, ws: ServerConnection) -> None:
        self.clients.add(ws)
        logger.info("Client connected (%d total)", len(self.clients))

    def remove_client(self, ws: ServerConnection) -> None:
        self.clients.discard(ws)
        logger.info("Client disconnected (%d total)", len(self.clients))

    async def broadcast(self, message: str) -> None:
        logger.debug("Broadcast: %s", message)
        if not self.clients:
            return
        await asyncio.gather(
            *(ws.send(message) for ws in self.clients),
            return_exceptions=True,
        )

    async def handle_client_message(self, ws: ServerConnection, data: str) -> None:
        logger.debug("Client message: %s", data)
        try:
            msg = ClientMessage.from_json(data)
        except ProtocolError as e:
            await ws.send(error_message(str(e)))
            return

        match msg.type:
            case "prompt":
                await self._handle_prompt(msg.message)
            case "steer":
                stripped = msg.message.strip()
                if stripped == "/reload":
                    await self._handle_reload()
                elif stripped.startswith("/model"):
                    await self._handle_model_command(stripped)
                elif stripped.startswith("/reasoning"):
                    await self._handle_reasoning_command(stripped)
                else:
                    logger.debug("Steer: %s", msg.message)
                    async for event in self.agent.steer(msg.message):
                        await self.broadcast(serialize_event(event))
            case "abort":
                logger.debug("Abort requested")
                await self.agent.abort()
            case "context_request":
                data = self.agent.context_info()
                await ws.send(context_info_message(data))
            case "model_list_request":
                models = await self._get_model_list()
                await ws.send(json.dumps({
                    "type": "model_list",
                    "models": models,
                    "current": self.agent.model,
                    "reasoning_effort": self.agent.reasoning_effort,
                }))
            case "model_info_request":
                await self._handle_model_info(msg.model_id)

    async def _handle_reload(self) -> None:
        """Hot-reload agent modules and rebuild web package."""
        await self.broadcast(json.dumps({"type": "reload_start"}))
        errors: list[str] = []

        # Save agent state
        old_agent = self.agent
        saved_messages = list(old_agent._messages)
        saved_model = old_agent._model
        saved_system_prompt = old_agent._system_prompt

        # Reload agent Python modules (dependency order)
        try:
            import agent.events
            import agent.exceptions
            import agent.tools
            import agent.client
            import agent as agent_pkg

            importlib.reload(agent.events)
            importlib.reload(agent.exceptions)
            importlib.reload(agent.tools)
            importlib.reload(agent.client)
            importlib.reload(agent_pkg)

            # Create new agent from reloaded code
            NewAgentClient = agent_pkg.AgentClient
            new_agent = NewAgentClient(
                model=saved_model,
                system_prompt=saved_system_prompt,
            )
            await new_agent.start()
            new_agent._messages = saved_messages

            await old_agent.stop()
            self.agent = new_agent
            logger.info("Agent modules reloaded successfully")
        except Exception as e:
            logger.exception("Agent reload failed")
            errors.append(f"Agent reload failed: {e}")

        # Rebuild web package
        web_dir = _find_web_dir()
        if web_dir:
            try:
                # Ensure dependencies are installed first
                _ensure_web_node_modules(web_dir)

                result = await asyncio.to_thread(
                    subprocess.run,
                    ["npm", "run", "build"],
                    cwd=str(web_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    errors.append(f"Web build failed: {result.stderr.strip()}")
                else:
                    logger.info("Web package rebuilt successfully")
            except Exception as e:
                errors.append(f"Web build failed: {e}")
        else:
            logger.debug("Web directory not found, skipping rebuild")

        success = len(errors) == 0
        message = "Reload complete" if success else "; ".join(errors)
        await self.broadcast(json.dumps({
            "type": "reload_end",
            "success": success,
            "message": message,
        }))

    async def _handle_model_command(self, command: str) -> None:
        """Handle /model [model_id] — show or switch model."""
        parts = command.split(None, 1)
        if len(parts) < 2:
            # No argument: show current model
            await self.broadcast(json.dumps({
                "type": "model_set",
                "model": self.agent.model,
            }))
            return
        model_id = parts[1].strip()
        self.agent.set_model(model_id)
        await self.broadcast(json.dumps({
            "type": "model_set",
            "model": model_id,
        }))

    async def _handle_reasoning_command(self, command: str) -> None:
        """Handle /reasoning [effort] — show or switch reasoning effort."""
        VALID_EFFORTS = ["low", "medium", "high", "xhigh"]
        parts = command.split(None, 1)
        if len(parts) < 2:
            # No argument: show current reasoning effort
            await self.broadcast(json.dumps({
                "type": "reasoning_effort_set",
                "effort": self.agent.reasoning_effort,
            }))
            return
        effort = parts[1].strip().lower()
        if effort not in VALID_EFFORTS:
            await self.broadcast(json.dumps({
                "type": "reasoning_effort_set",
                "effort": self.agent.reasoning_effort,
                "error": f"Invalid effort. Valid values: {', '.join(VALID_EFFORTS)}",
            }))
            return
        self.agent.set_reasoning_effort(effort)
        await self.broadcast(json.dumps({
            "type": "reasoning_effort_set",
            "effort": effort,
        }))

    async def _get_model_list(self) -> list[dict[str, str]]:
        """Fetch available models from OpenRouter (cached)."""
        if self._model_cache is not None:
            return self._model_cache
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                self._model_cache = [
                    {
                        "id": m["id"],
                        "name": m.get("name", m["id"]),
                        "description": m.get("description", ""),
                    }
                    for m in data.get("data", [])
                ]
                logger.info("Fetched %d models from OpenRouter", len(self._model_cache))
                return self._model_cache
        except Exception as e:
            logger.warning("Failed to fetch model list: %s", e)
            return []

    async def _handle_model_info(self, model_id: str) -> None:
        """Fetch and display info about a specific model from OpenRouter (from cache)."""
        # First, ensure we have the model list
        models = await self._get_model_list()
        
        # Search for the model in the full cache (not just the stripped-down list)
        # We need to refetch with full data to get pricing
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                
                # Find the model
                model_data = None
                for m in data.get("data", []):
                    if m["id"] == model_id:
                        model_data = m
                        break
                
                if not model_data:
                    await self.broadcast(json.dumps({
                        "type": "error",
                        "message": f"Model not found: {model_id}",
                    }))
                    return
                
                # Extract pricing info
                pricing = model_data.get("pricing")
                pricing_info = None
                if pricing:
                    pricing_info = {
                        "prompt": pricing.get("prompt", "0"),
                        "completion": pricing.get("completion", "0"),
                    }
                
                # Extract top provider info
                top_provider = model_data.get("top_provider")
                top_provider_info = None
                if top_provider:
                    top_provider_info = {
                        "provider": top_provider.get("provider_name", ""),
                        "max_completion_tokens": top_provider.get("max_completion_tokens", 0),
                        "supports_vision": top_provider.get("supports_vision", False),
                    }
                
                # Extract architecture info
                architecture = model_data.get("architecture")
                architecture_info = None
                if architecture:
                    architecture_info = {
                        "model": architecture.get("model", ""),
                        "mode": architecture.get("mode", ""),
                        "tokenizer": architecture.get("tokenizer", ""),
                        "instruct_type": architecture.get("instruct_type", ""),
                    }
                
                # Extract recommended info
                recommended = model_data.get("recommended")
                recommended_info = None
                if recommended:
                    recommended_info = {
                        "prompt": recommended.get("prompt", 0),
                        "completion": recommended.get("completion", 0),
                    }
                
                await self.broadcast(json.dumps({
                    "type": "model_info",
                    "model_id": model_data.get("id", model_id),
                    "name": model_data.get("name", model_id),
                    "description": model_data.get("description", ""),
                    "pricing": pricing_info,
                    "context_length": model_data.get("context_length", 0),
                    "architecture": architecture_info,
                    "top_provider": top_provider_info,
                    "recommended": recommended_info,
                    "enabled": model_data.get("enabled", True),
                    "modality": model_data.get("modality", ""),
                    "created": model_data.get("created", 0),
                    "route": model_data.get("route", ""),
                }))
                logger.info("Fetched model info for %s", model_id)
        except Exception as e:
            logger.warning("Failed to fetch model info: %s", e)
            await self.broadcast(json.dumps({
                "type": "error",
                "message": f"Failed to fetch model info: {e}",
            }))

    async def _handle_prompt(self, message: str) -> None:
        # Broadcast user message to all clients so they stay in sync
        await self.broadcast(user_message(message))
        async with self._prompt_lock:
            try:
                async for event in self.agent.prompt(message):
                    await self.broadcast(serialize_event(event))
            except Exception as e:
                logger.exception("Error during prompt handling")
                await self.broadcast(error_message(str(e)))

"""CLI entry point for the `zac` command."""

from __future__ import annotations

import argparse
import os
import sys

from . import daemon, tui
from .paths import DefaultPaths

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_MODEL = "mistralai/mistral-large-2512"
DEFAULT_LOG_LEVEL = "info"


def _load_config(paths: DefaultPaths) -> dict | None:
    """Load config from zac-config.toml if it exists."""
    config_file = paths.config_file
    if not config_file.is_file():
        return None

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    try:
        with open(config_file, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(
            f"Warning: failed to load config from {config_file}: {e}", file=sys.stderr
        )
        return None


def _get_api_key(paths: DefaultPaths) -> str:
    """Get the OpenRouter API key, prompting user if necessary."""
    # First check env var (backward compatibility)
    env_key = os.environ.get("OPENROUTER_API_KEY")
    if env_key:
        return env_key

    # Check config file
    config = _load_config(paths)
    if config and "open-router-api-key" in config:
        return config["open-router-api-key"]

    # Prompt user and create config file
    print("OpenRouter API key not found.")
    print("You can get one from https://openrouter.ai/settings")
    api_key = input("Enter your OpenRouter API key: ").strip()

    if not api_key:
        print("Error: API key is required", file=sys.stderr)
        sys.exit(1)

    # Create config file
    config_file = paths.config_file
    config_content = f"""# Zac configuration
# Get your API key from https://openrouter.ai/settings

open-router-api-key = "{api_key}"
"""
    config_file.write_text(config_content)
    print(f"Config saved to {config_file}")

    return api_key


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """Add options shared between the default command and `gateway start`."""
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"Bind address (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )
    parser.add_argument("--tls-cert", help="TLS certificate file")
    parser.add_argument("--tls-key", help="TLS private key file")
    parser.add_argument("--no-tls", action="store_true", help="Disable TLS")
    parser.add_argument("--system-prompt-file", help="Path to system prompt file")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Model ID (default: {DEFAULT_MODEL})"
    )
    parser.add_argument("--log-file", help="Gateway log file path")
    parser.add_argument(
        "--log-level",
        choices=["debug", "info"],
        default=DEFAULT_LOG_LEVEL,
        help=f"Log level (default: {DEFAULT_LOG_LEVEL})",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zac", description="Zac agent CLI")
    _add_common_options(parser)
    parser.add_argument(
        "--restart-gateway",
        action="store_true",
        help="Restart the gateway before connecting",
    )
    parser.add_argument(
        "--gateway",
        metavar="URL",
        help="Connect to a remote gateway URL (e.g. wss://host:8765) instead of starting a local one",
    )
    parser.add_argument(
        "--user-prompt",
        metavar="PROMPT",
        help="Send a prompt to the agent and print the response to stdout (does not start the TUI)",
    )

    sub = parser.add_subparsers(dest="command")

    actions = sub.add_parser("actions-server", help="Start the action-system server")
    actions.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the action-system server (default: 8000)",
    )
    gw = sub.add_parser("gateway", help="Manage the gateway daemon")
    gw_sub = gw.add_subparsers(dest="gateway_action")

    gw_start = gw_sub.add_parser("start", help="Start gateway daemon")
    _add_common_options(gw_start)

    gw_sub.add_parser("stop", help="Stop gateway daemon")
    gw_sub.add_parser("status", help="Check gateway status")

    gw_restart = gw_sub.add_parser("restart", help="Restart gateway daemon")
    _add_common_options(gw_restart)

    return parser


def _gateway_opts(args: argparse.Namespace, api_key: str | None = None) -> dict:
    """Extract gateway start options from parsed args."""
    opts = dict(
        host=args.host,
        port=args.port,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        no_tls=args.no_tls,
        system_prompt_file=args.system_prompt_file,
        model=args.model,
        log_file=args.log_file,
        log_level=args.log_level,
    )
    if api_key:
        opts["api_key"] = api_key
    return opts


def _run_user_prompt(prompt: str, args: argparse.Namespace, api_key: str) -> None:
    """Run the agent with a user prompt and print the response to stdout."""
    import asyncio

    from agent.client import AgentClient
    from agent.events import EventType

    async def _async_run():
        client = AgentClient(
            model=args.model,
            system_prompt=None,  # Loaded automatically by AgentClient
        )
        await client.start()

        try:
            async for event in client.prompt(prompt):
                if event.type == EventType.TEXT_DELTA:
                    print(event.delta, end="", flush=True)
                elif event.type == EventType.ERROR:
                    print(f"\nError: {event.message}", file=sys.stderr)
                    sys.exit(1)
            print()  # newline after streamed response
        finally:
            await client.stop()

    asyncio.run(_async_run())


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # For gateway commands, get API key if needed
    paths = DefaultPaths()
    api_key = _get_api_key(paths)

    if args.user_prompt:
        _run_user_prompt(args.user_prompt, args, api_key)
        return

    if args.command == "gateway":
        if args.gateway_action == "start":
            daemon.start(**_gateway_opts(args, api_key))
        elif args.gateway_action == "stop":
            daemon.stop()
        elif args.gateway_action == "restart":
            daemon.restart(**_gateway_opts(args, api_key))
        elif args.gateway_action == "status":
            pid = daemon.status()
            if pid:
                print(f"Gateway is running (pid {pid})")
            else:
                print("Gateway is not running")
                sys.exit(1)
        else:
            parser.parse_args(["gateway", "--help"])
    else:
        if args.gateway:
            # Connect to remote gateway
            tui.launch(gateway_url=args.gateway)
        else:
            # Launch a gateway then connect
            import random

            # Choose a random port for the gateway
            random_port = random.randint(49152, 65535)
            use_tls = not args.no_tls
            opts = _gateway_opts(args, api_key)
            opts["port"] = random_port  # Override port with random port
            opts["ephemeral"] = True

            pid = None
            try:
                if args.restart_gateway:
                    pid = daemon.restart(**opts)
                else:
                    pid = daemon.start(**opts)

                # Launch TUI and connect to the gateway
                scheme = "wss" if use_tls else "ws"
                gateway_url = f"{scheme}://localhost:{random_port}"
                tui.launch(gateway_url=gateway_url)
            finally:
                # Stop the gateway when the TUI exits
                if pid is not None:
                    daemon.stop(pid=pid)

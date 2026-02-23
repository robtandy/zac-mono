"""Gateway daemon management: start, stop, status, and health check."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from .paths import DefaultPaths


def _read_pid(pid_file: Path) -> int | None:
    """Read PID from file, returning None if missing or invalid."""
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _is_alive(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_for_tcp(host: str, port: int, timeout: float = 10.0) -> bool:
    """Poll TCP connection until the gateway is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def status(paths: DefaultPaths | None = None) -> int | None:
    """Return the gateway PID if running, else None."""
    paths = paths or DefaultPaths()
    pid = _read_pid(paths.pid_file)
    if pid is None:
        return None
    if _is_alive(pid):
        return pid
    # Stale PID file
    paths.pid_file.unlink(missing_ok=True)
    return None


def start(
    *,
    host: str = "0.0.0.0",
    port: int = 8765,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    no_tls: bool = False,
    system_prompt_file: str | None = None,
    model: str | None = None,
    log_file: str | None = None,
    log_level: str = "info",
    paths: DefaultPaths | None = None,
) -> int:
    """Start the gateway as a background daemon. Returns the PID."""
    paths = paths or DefaultPaths()

    existing = status(paths)
    if existing is not None:
        print(f"Gateway already running (pid {existing})")
        return existing

    # Build gateway command
    cmd = [sys.executable, "-m", "gateway", "--host", host, "--port", str(port)]

    if log_level == "debug":
        cmd.append("--debug")

    # TLS
    if not no_tls:
        cert = tls_cert or str(paths.tls_cert)
        key = tls_key or str(paths.tls_key)
        if Path(cert).is_file() and Path(key).is_file():
            cmd.extend(["--tls-cert", cert, "--tls-key", key])

    # Set up logging - stdout by default, or log file if specified
    stdout = sys.stdout
    stderr = sys.stderr
    if log_file:
        log = log_file
        cmd.extend(["--log-file", log])
        log_path = Path(log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "a")
        stdout = log_fh
        stderr = log_fh

    if model:
        cmd.extend(["--model", model])

    # Environment: pass system prompt file
    env = os.environ.copy()
    prompt_file = system_prompt_file or str(paths.system_prompt)
    if Path(prompt_file).is_file():
        env["ZAC_SYSTEM_PROMPT_FILE"] = prompt_file

    proc = subprocess.Popen(
        cmd,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        env=env,
    )

    # Write PID file
    paths.pid_dir.mkdir(parents=True, exist_ok=True)
    paths.pid_file.write_text(str(proc.pid))

    # Wait for gateway to accept connections
    if not _wait_for_tcp(host, port):
        print(f"Warning: gateway (pid {proc.pid}) did not become ready within 10s", file=sys.stderr)
        if log_file:
            print(f"Check log file: {log}", file=sys.stderr)
    else:
        print(f"Gateway started (pid {proc.pid})")

    return proc.pid


def restart(*, paths: DefaultPaths | None = None, **kwargs) -> int:
    """Restart the gateway daemon. Returns the new PID."""
    paths = paths or DefaultPaths()
    stop(paths)
    return start(paths=paths, **kwargs)


def stop(paths: DefaultPaths | None = None) -> bool:
    """Stop the gateway daemon. Returns True if it was stopped."""
    paths = paths or DefaultPaths()
    pid = status(paths)
    if pid is None:
        print("Gateway is not running")
        return False

    os.kill(pid, signal.SIGTERM)

    # Wait for process to exit
    for _ in range(20):
        if not _is_alive(pid):
            break
        time.sleep(0.25)
    else:
        # Force kill if still alive
        os.kill(pid, signal.SIGKILL)

    paths.pid_file.unlink(missing_ok=True)
    print(f"Gateway stopped (pid {pid})")
    return True

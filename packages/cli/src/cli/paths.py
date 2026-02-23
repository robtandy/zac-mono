"""Repo root discovery and default paths."""

from __future__ import annotations

import os
from pathlib import Path


def find_repo_root() -> Path:
    """Walk up from this file to find the pyproject.toml with name='zac-mono'.

    Can be overridden with the ZAC_ROOT environment variable.
    """
    override = os.environ.get("ZAC_ROOT")
    if override:
        return Path(override).resolve()

    current = Path(__file__).resolve().parent
    while current != current.parent:
        pyproject = current / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text()
                if 'name = "zac-mono"' in text:
                    return current
            except OSError:
                pass
        current = current.parent

    raise RuntimeError(
        "Could not find repo root (pyproject.toml with name='zac-mono'). "
        "Set ZAC_ROOT to override."
    )


class DefaultPaths:
    """Default file paths derived from the repo root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or find_repo_root()

    @property
    def tls_cert(self) -> Path:
        return self.root / "certs" / "tailscale.crt"

    @property
    def tls_key(self) -> Path:
        return self.root / "certs" / "tailscale.key"

    @property
    def system_prompt(self) -> Path:
        return self.root / "packages" / "agent" / "system_prompt"

    @property
    def log_file(self) -> Path:
        return self.root / "gateway.log"

    @property
    def tui_entry(self) -> Path:
        return self.root / "packages" / "tui" / "src" / "index.ts"

    @property
    def pid_dir(self) -> Path:
        return self.root / ".zac"

    @property
    def pid_file(self) -> Path:
        return self.pid_dir / "gateway.pid"

    @property
    def config_file(self) -> Path:
        return self.root / "zac-config.toml"

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchitectureSettings:
    """Validated, explicit runtime configuration for the new platform.

    Existing legacy environment variables remain isolated in compatibility
    adapters.  New components consume this object instead of reading process
    environment at import time.
    """

    profile: str
    data_dir: Path
    enable_mcp: bool
    enable_a2a: bool
    allow_remote_a2a: bool
    trusted_a2a_endpoints: frozenset[str]
    agent_capabilities: frozenset[str]
    allowed_plugins: frozenset[str]
    allowed_origins: tuple[str, ...]

    @classmethod
    def load(cls, base_dir: Path) -> "ArchitectureSettings":
        base_dir = Path(base_dir).resolve()
        profile = os.environ.get("AI_ASSISTANT_PROFILE", "desktop").strip().lower()
        config_dir = base_dir / "config"
        payload: dict = {}
        for path in (
            config_dir / "base.toml", config_dir / f"{profile}.toml", config_dir / "plugins.toml",
            config_dir / "agents.toml",
        ):
            if path.exists():
                with path.open("rb") as handle:
                    payload.update(tomllib.load(handle))
        runtime = payload.get("runtime", {})
        plugins = payload.get("plugins", {})
        a2a = payload.get("a2a", {})
        agents = payload.get("agents", {})
        api = payload.get("api", {})
        data_dir = Path(os.environ.get("APP_DATA_DIR", str(base_dir.parent))) / "AIAssistant"
        return cls(
            profile=profile,
            data_dir=data_dir,
            enable_mcp=bool(runtime.get("enable_mcp", True)),
            enable_a2a=bool(runtime.get("enable_a2a", True)),
            allow_remote_a2a=bool(runtime.get("allow_remote_a2a", False)),
            trusted_a2a_endpoints=frozenset(str(item) for item in a2a.get("trusted_endpoints", [])),
            agent_capabilities=frozenset(str(item) for item in agents.get("capabilities", [])),
            allowed_plugins=frozenset(str(item) for item in plugins.get("enabled", ["builtin.legacy-tools"])),
            allowed_origins=tuple(str(item) for item in api.get("allowed_origins", [])),
        )

"""Configuration: file, environment, and CLI flags, in that order of priority.

A config file is optional. When one exists it is read from the first of:

    $PROMPT_HISTORY_CONFIG
    ~/.config/prompt-history/config.toml      (Python 3.11+)
    ~/.config/prompt-history/config.json

TOML is preferred when the interpreter can parse it without a third-party
package; JSON is always understood so the tool stays dependency free on 3.9
and 3.10.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any

try:                                  # Python 3.11+
    import tomllib
    HAVE_TOML = True
except ModuleNotFoundError:           # pragma: no cover - older interpreters
    tomllib = None                    # type: ignore[assignment]
    HAVE_TOML = False

ENV_PREFIX = "PROMPT_HISTORY_"


def config_home() -> Path:
    override = os.environ.get("PROMPT_HISTORY_HOME")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "prompt-history"


def default_config_path() -> Path:
    """Where `config --init` writes, given what this interpreter can read."""
    return config_home() / ("config.toml" if HAVE_TOML else "config.json")


def find_config() -> Path | None:
    explicit = os.environ.get("PROMPT_HISTORY_CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    for name in ("config.toml", "config.json"):
        candidate = config_home() / name
        if candidate.is_file():
            if name.endswith(".toml") and not HAVE_TOML:
                continue
            return candidate
    return None


@dataclass
class Config:
    """Every knob the library and the CLI share."""

    # --- sources -------------------------------------------------------
    claude_code: bool = True
    codex: bool = True
    include_subagents: bool = False
    include_recovered: bool = True      # history logs and sqlite thread indexes
    claude_config_dirs: list[str] = field(default_factory=list)
    codex_homes: list[str] = field(default_factory=list)

    # --- filtering -----------------------------------------------------
    min_words: int = 0
    max_words: int = 0                  # 0 = no ceiling
    skip_slash_commands: bool = False
    since: str | None = None            # ISO date or datetime
    until: str | None = None
    projects: list[str] = field(default_factory=list)
    exclude_projects: list[str] = field(default_factory=list)
    search: str | None = None
    tool: str | None = None             # "claude" or "codex"

    # --- server --------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 7777
    open_browser: bool = True

    # --- export --------------------------------------------------------
    export_format: str = "md"
    output: str | None = None

    _source: str | None = None          # where these values came from

    # ------------------------------------------------------------------
    @classmethod
    def _coerce(cls, key: str, value: Any) -> Any:
        types = {f.name: f.type for f in fields(cls)}
        declared = str(types.get(key, ""))
        if "bool" in declared:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if "int" in declared:
            return int(value)
        if "list" in declared:
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            return list(value)
        return value

    @classmethod
    def _apply(cls, target: "Config", data: dict, prefix: str = "") -> None:
        known = {f.name for f in fields(cls) if not f.name.startswith("_")}
        for key, value in data.items():
            name = f"{prefix}{key}".replace("-", "_")
            if isinstance(value, dict):
                # [sources] / [filter] / [server] / [export] are flat namespaces
                cls._apply(target, value, prefix="")
                continue
            if name in known and value is not None:
                setattr(target, name, cls._coerce(name, value))

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> "Config":
        """File, then `PROMPT_HISTORY_*` env vars, then keyword overrides."""
        config = cls()

        chosen = Path(path).expanduser() if path else find_config()
        if chosen and chosen.is_file():
            raw = chosen.read_bytes()
            try:
                if chosen.suffix == ".toml" and HAVE_TOML:
                    data = tomllib.loads(raw.decode("utf-8"))
                else:
                    data = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError(f"Could not parse {chosen}: {exc}") from exc
            cls._apply(config, data)
            config._source = str(chosen)

        known = {f.name for f in fields(cls) if not f.name.startswith("_")}
        for key, value in os.environ.items():
            if not key.startswith(ENV_PREFIX):
                continue
            name = key[len(ENV_PREFIX):].lower()
            if name in known:
                setattr(config, name, cls._coerce(name, value))

        unknown = set(overrides) - known
        if unknown:
            # A silently ignored keyword looks like a filter that did nothing.
            raise TypeError(
                "Unknown option(s): " + ", ".join(sorted(unknown))
                + ". Valid options: " + ", ".join(sorted(known))
            )
        for key, value in overrides.items():
            if value is not None:
                setattr(config, key, cls._coerce(key, value))

        return config

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("_source", None)
        return data

    @property
    def source(self) -> str:
        return self._source or "defaults"


TEMPLATE_TOML = """\
# prompt-history configuration
# Every value here can be overridden by a CLI flag or a PROMPT_HISTORY_* env var.

[sources]
claude_code = true
codex = true
# Sub-agent prompts are written by the model, not by you.
include_subagents = false
# History logs and sqlite thread indexes, used to recover deleted transcripts.
include_recovered = true
# Extra roots to scan, if your tools live somewhere unusual.
claude_config_dirs = []
codex_homes = []

[filter]
# tool = "claude"        # or "codex"; omit for both
min_words = 0
max_words = 0            # 0 means no ceiling
skip_slash_commands = false
# since = "2026-01-01"
# until = "2026-12-31"
projects = []            # only these projects, by name or path fragment
exclude_projects = []

[server]
host = "127.0.0.1"
port = 7777
open_browser = true

[export]
export_format = "md"     # md | json | csv | txt | zip
# output = "~/prompts"
"""

TEMPLATE_JSON = json.dumps(
    {
        "claude_code": True,
        "codex": True,
        "include_subagents": False,
        "include_recovered": True,
        "claude_config_dirs": [],
        "codex_homes": [],
        "min_words": 0,
        "max_words": 0,
        "skip_slash_commands": False,
        "projects": [],
        "exclude_projects": [],
        "host": "127.0.0.1",
        "port": 7777,
        "open_browser": True,
        "export_format": "md",
    },
    indent=2,
) + "\n"


def write_template(path: Path | None = None, force: bool = False) -> Path:
    target = Path(path).expanduser() if path else default_config_path()
    if target.exists() and not force:
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = TEMPLATE_TOML if target.suffix == ".toml" else TEMPLATE_JSON
    target.write_text(body, encoding="utf-8")
    return target

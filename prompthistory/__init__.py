"""Prompt History: read back the prompts you typed into Claude Code and Codex.

    from prompthistory import PromptHistory

    ph = PromptHistory()
    print(len(ph.prompts()))
    for hit in ph.search("migration"):
        print(hit.timestamp, hit.project, hit.text[:60])

    ph.write("prompts.zip", fmt="zip")
    ph.sync("~/prompts")            # mirror into a folder, tool then project

Nothing here touches the network and nothing writes to your session files.
"""

from __future__ import annotations

from pathlib import Path

from .api import PromptHistory, SOURCE_LABELS, TOOL_OF_SOURCE
from .config import Config
from .export import FORMATS, render, write as _write_snapshot
from .model import Prompt, Session
from .sync import SyncError, SyncReport

__version__ = "1.2.0"

__all__ = [
    "PromptHistory",
    "Config",
    "Prompt",
    "Session",
    "FORMATS",
    "SyncReport",
    "SyncError",
    "SOURCE_LABELS",
    "TOOL_OF_SOURCE",
    "scan",
    "__version__",
]


def scan(**overrides) -> PromptHistory:
    """Shorthand: `prompthistory.scan(tool="codex", since="2026-01-01")`."""
    return PromptHistory(**overrides)


def _write(self: PromptHistory, path: str | Path, fmt: str | None = None,
           **overrides) -> Path:
    """Write the current view to disk. Format is inferred from the suffix."""
    if fmt is None:
        suffix = Path(path).suffix.lstrip(".").lower()
        fmt = suffix if suffix in FORMATS else self.config.export_format
    return _write_snapshot(self.snapshot(**overrides), fmt, path)


def _render(self: PromptHistory, fmt: str = "md", **overrides):
    """Return the export as a string, or bytes for `zip`."""
    return render(self.snapshot(**overrides), fmt)


PromptHistory.write = _write          # type: ignore[attr-defined]
PromptHistory.render = _render        # type: ignore[attr-defined]

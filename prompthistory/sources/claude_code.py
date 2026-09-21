"""Read user prompts out of Claude Code transcripts.

Claude Code appends one JSON object per line to
``<config>/projects/<slugified-cwd>/<session-uuid>.jsonl``.  A line with
``type == "user"`` is either a real turn (``message.content`` is a string, or a
list containing ``text`` blocks) or a tool result being fed back to the model.
Only the former is a prompt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..model import Prompt, Session, clean_text, is_noise, to_iso


def config_dirs(extra: list[str] | None = None) -> list[Path]:
    """Every plausible Claude Code config root on this machine."""
    candidates: list[Path] = [Path(p).expanduser() for p in (extra or [])]
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        candidates.extend(Path(p).expanduser() for p in env.split(os.pathsep) if p)
    home = Path.home()
    candidates += [
        home / ".claude",
        home / ".config" / "claude",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "claude")
    seen, roots = set(), []
    for path in candidates:
        resolved = str(path)
        if resolved not in seen and path.is_dir():
            seen.add(resolved)
            roots.append(path)
    return roots


def _blocks_to_text(content) -> str:
    """Flatten a message payload to the text the user actually typed."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            if isinstance(block, str):
                parts.append(block)
            continue
        # tool_result / tool_use blocks are machine traffic, never a prompt.
        if block.get("type") in ("tool_result", "tool_use", "thinking"):
            return ""
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _parse_transcript(path: Path, include_subagents: bool) -> Session | None:
    session = Session(id=path.stem, source="claude-code", origin_file=str(path))
    saw_line = False

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    with handle:
        for turn, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            saw_line = True

            kind = record.get("type")
            session.project = record.get("cwd") or session.project
            session.git_branch = record.get("gitBranch") or session.git_branch
            if record.get("sessionId"):
                session.id = record["sessionId"]

            if kind == "custom-title" and record.get("customTitle"):
                session.title = record["customTitle"]
                continue
            if kind == "summary" and record.get("summary"):
                session.title = session.title or record["summary"]
                continue
            if kind == "assistant":
                model = (record.get("message") or {}).get("model")
                if model:
                    session.model = model
                continue
            if kind != "user":
                continue

            if record.get("isMeta") or record.get("isCompactSummary"):
                continue
            if record.get("isSidechain") and not include_subagents:
                continue
            if record.get("userType") not in (None, "external"):
                continue

            message = record.get("message") or {}
            text = clean_text(_blocks_to_text(message.get("content")))
            if not text or is_noise(text):
                continue

            session.prompts.append(
                Prompt(
                    text=text,
                    source="claude-code",
                    session_id=session.id,
                    timestamp=to_iso(record.get("timestamp")),
                    project=record.get("cwd") or session.project,
                    git_branch=record.get("gitBranch"),
                    model=session.model,
                    origin_file=str(path),
                    turn=turn,
                )
            )

    if not saw_line:
        return None
    for prompt in session.prompts:
        prompt.session_id = session.id
        prompt.project = prompt.project or session.project
    return session.finalise()


def collect(include_subagents: bool = False,
            extra_roots: list[str] | None = None) -> list[Session]:
    sessions: list[Session] = []
    for root in config_dirs(extra_roots):
        projects = root / "projects"
        if not projects.is_dir():
            continue
        for transcript in sorted(projects.rglob("*.jsonl")):
            session = _parse_transcript(transcript, include_subagents)
            if session and session.prompts:
                sessions.append(session)
    return sessions


def collect_recent_history() -> list[Session]:
    """`~/.claude.json` keeps a per-project list of recently typed prompts.

    Useful when a transcript has been deleted but the prompt line survives.
    Callers de-duplicate these against the transcript prompts.
    """
    sessions: list[Session] = []
    for candidate in (Path.home() / ".claude.json", Path.home() / ".config" / "claude.json"):
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        for project_path, project in (data.get("projects") or {}).items():
            if not isinstance(project, dict):
                continue
            history = project.get("history")
            if not isinstance(history, list) or not history:
                continue
            session = Session(
                id=f"history:{project_path}",
                source="claude-code-history",
                title="Recent prompt history",
                project=project_path,
                origin_file=str(candidate),
                note="Recovered from Claude Code's recent-prompt list; no timestamps recorded.",
            )
            for turn, entry in enumerate(history, start=1):
                raw = entry.get("display") if isinstance(entry, dict) else entry
                if not isinstance(raw, str):
                    continue
                text = clean_text(raw)
                if not text or is_noise(text):
                    continue
                session.prompts.append(
                    Prompt(
                        text=text,
                        source="claude-code-history",
                        session_id=session.id,
                        project=project_path,
                        origin_file=str(candidate),
                        turn=turn,
                    )
                )
            if session.prompts:
                sessions.append(session.finalise())
    return sessions

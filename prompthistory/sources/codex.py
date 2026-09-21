"""Read user prompts out of OpenAI Codex sessions.

Codex has used several on-disk shapes over its releases, so every reader here
is deliberately tolerant:

* ``~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl`` -- the full transcript.
  A user turn appears either as ``{"type": "response_item", "payload":
  {"type": "message", "role": "user", ...}}``, as a bare ``{"type": "message",
  "role": "user", ...}`` line in older builds, or as an ``event_msg`` with
  ``{"type": "user_message"}``.
* ``~/.codex/history.jsonl`` -- a flat ``{session_id, ts, text}`` log of
  everything typed at the composer.
* ``~/.codex/state*.sqlite`` -- the desktop app's thread index, which keeps a
  ``first_user_message`` even after a rollout file is gone.
* ``~/.codex/sqlite/codex-dev.db`` -- catalog of ChatGPT-synced threads. Titles
  and timestamps only; the prompt bodies live server-side, so these are
  reported separately rather than mixed in with real prompts.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from ..model import Prompt, Session, epoch, is_noise, to_iso
from .codex_text import goal_context, is_subagent, user_text


def _goal_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.removeprefix("/goal ")).strip()


# Codex prepends its own scaffolding as user-role turns; these never came
# from the keyboard.
_SCAFFOLD_PREFIXES = (
    "You are Codex",
    "<INSTRUCTIONS>",
    "# AGENTS.md",
    "[TURN CONTEXT]",
)


def codex_roots(extra: list[str] | None = None) -> list[Path]:
    candidates: list[Path] = [Path(p).expanduser() for p in (extra or [])]
    env = os.environ.get("CODEX_HOME")
    if env:
        candidates.extend(Path(p).expanduser() for p in env.split(os.pathsep) if p)
    home = Path.home()
    candidates += [home / ".codex", home / ".config" / "codex"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "codex")
    seen, roots = set(), []
    for path in candidates:
        if path.is_dir() and str(path) not in seen:
            seen.add(str(path))
            roots.append(path)
    return roots


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") in ("tool_result", "tool_use", "output_text", "function_call_output"):
                return ""
            if block.get("type") in ("input_text", "text"):
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
    return "\n".join(parts)


def _looks_like_scaffold(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in _SCAFFOLD_PREFIXES)


def _extract_user_text(record: dict) -> str | None:
    """Pull the typed text out of one rollout line, or None if it isn't one."""
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else None
    if record.get("type") not in (None, "message", "response_item", "event_msg", "user_message"):
        return None
    candidates = [c for c in (payload, record) if isinstance(c, dict)]

    for node in candidates:
        node_type = node.get("type")
        if node_type == "user_message" and isinstance(node.get("message"), str):
            return node["message"]
        if node_type in ("message", None) and node.get("role") == "user":
            text = _content_to_text(node.get("content"))
            if text:
                return text
    return None


def _parse_rollout(path: Path, include_subagents: bool = False) -> Session | None:
    session = Session(id=path.stem, source="codex", origin_file=str(path))
    previous = None
    objectives: set[str] = set()
    pending_goals = []
    active_turn = None

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

            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            meta = payload or record

            if record.get("type") in ("session_meta", "state") or "instructions" in meta:
                session.is_subagent = is_subagent(meta)
                if is_subagent(meta) and not include_subagents:
                    return None
                session.id = meta.get("id") or session.id
                session.project = meta.get("cwd") or session.project
                git = meta.get("git")
                if isinstance(git, dict):
                    session.git_branch = git.get("branch") or session.git_branch
                session.started_at = to_iso(
                    meta.get("timestamp") or record.get("timestamp")
                ) or session.started_at
            if record.get("type") == "turn_context":
                active_turn = meta.get("turn_id") or meta.get("id")
                session.project = meta.get("cwd") or session.project
                session.model = meta.get("model") or session.model

            raw = _extract_user_text(record)
            if raw is None:
                continue

            context = goal_context(raw)
            if context is not None:
                objective, edited = context
                if not objective:
                    continue
                if not edited:
                    pending_goals.append((objective, record.get("timestamp"), turn))
                    continue
                raw = "/goal " + objective
            text, references, recovery_note = user_text(raw, session.project)
            if not text or is_noise(text) or _looks_like_scaffold(text):
                continue
            stamp = to_iso(record.get("timestamp")) or session.started_at
            metadata = meta.get("internal_chat_message_metadata_passthrough") or {}
            turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
            turn_id = turn_id or meta.get("turn_id") or active_turn
            shape = "event" if meta.get("type") == "user_message" else "message"
            # Pair duplicate representations of ONE submission, not all matching
            # text in a session. Repeated requests on later turns must survive.
            if previous:
                old_text, old_shape, old_stamp, old_turn = previous
                same_turn = bool(turn_id and old_turn and turn_id == old_turn)
                close = bool(stamp and old_stamp and abs(epoch(stamp) - epoch(old_stamp)) <= 2)
                if text == old_text and shape != old_shape and (same_turn or (not (turn_id and old_turn) and close)):
                    previous = None
                    continue
            previous = (text, shape, stamp, turn_id)
            objectives.add(_goal_key(text))

            session.prompts.append(
                Prompt(
                    text=text,
                    source="codex",
                    session_id=session.id,
                    timestamp=stamp,
                    project=session.project,
                    git_branch=session.git_branch,
                    model=session.model,
                    origin_file=str(path),
                    turn=turn,
                    references=references,
                    recovery_note=recovery_note,
                )
            )

    # Some versions persist an objective only in the continuation envelope.
    # Recover each missing objective once, never the continuation instructions.
    for objective, stamp, turn in pending_goals:
        key = _goal_key(objective)
        if key not in objectives:
            preceding = [p for p in session.prompts if p.turn < turn]
            missing = preceding[-1] if preceding else None
            if missing and missing.recovery_note and missing.text.startswith("/goal Read the Codex goal objective file at "):
                missing.text = "/goal " + objective
                missing.recovery_note = "Goal file unavailable; objective recovered from saved goal context."
                missing.id = ""
                missing.__post_init__()
            else:
                session.prompts.append(Prompt(text="/goal " + objective, source="codex",
                    session_id=session.id, timestamp=to_iso(stamp), project=session.project,
                    origin_file=str(path), turn=turn,
                    recovery_note="Goal objective recovered from saved goal context."))
            objectives.add(key)
    if not session.prompts:
        return None
    for prompt in session.prompts:
        prompt.session_id = session.id
        prompt.project = prompt.project or session.project
        prompt.model = prompt.model or session.model
    return session.finalise()


def collect_rollouts(extra_roots: list[str] | None = None, include_subagents: bool = False) -> list[Session]:
    sessions = []
    for root in codex_roots(extra_roots):
        for folder in (root / "sessions", root / "archived_sessions"):
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.jsonl")):
                session = _parse_rollout(path, include_subagents)
                if session:
                    sessions.append(session)
    return sessions


def collect_composer_history(extra_roots: list[str] | None = None) -> list[Session]:
    """`history.jsonl`: one line per prompt typed, grouped here by session."""
    by_session: dict[str, Session] = {}
    for root in codex_roots(extra_roots):
        path = root / "history.jsonl"
        if not path.is_file():
            continue
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
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
                text, references, note = user_text(record.get("text") or record.get("prompt") or "")
                if not text or is_noise(text) or _looks_like_scaffold(text):
                    continue
                sid = str(record.get("session_id") or record.get("sessionId") or "unknown")
                session = by_session.get(sid)
                if session is None:
                    session = Session(
                        id=sid,
                        source="codex-history",
                        origin_file=str(path),
                        note="From Codex's composer history log.",
                    )
                    by_session[sid] = session
                session.prompts.append(
                    Prompt(
                        text=text,
                        source="codex-history",
                        session_id=sid,
                        timestamp=to_iso(record.get("ts") or record.get("timestamp")),
                        origin_file=str(path),
                        turn=turn,
                        references=references,
                        recovery_note=note,
                    )
                )
    return [s.finalise() for s in by_session.values() if s.prompts]


def _open_readonly(path: Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def collect_thread_index(extra_roots: list[str] | None = None, include_subagents: bool = False) -> list[Session]:
    """Desktop `threads` rows keep the opening prompt even without a rollout."""
    sessions = []
    for root in codex_roots(extra_roots):
        for db_path in sorted(root.glob("state*.sqlite")):
            con = _open_readonly(db_path)
            if con is None:
                continue
            with closing(con):
                cols = _columns(con, "threads")
                if not {"id", "first_user_message"} <= cols:
                    continue
                wanted = [
                    c
                    for c in (
                        "id", "title", "cwd", "created_at_ms", "updated_at_ms",
                        "first_user_message", "model", "git_branch", "rollout_path",
                        "source", "agent_path", "thread_section_id", "is_pinned", "created_at", "updated_at",
                    )
                    if c in cols
                ]
                try:
                    rows = con.execute(
                        f"SELECT {', '.join(wanted)} FROM threads"
                    ).fetchall()
                except sqlite3.Error:
                    continue
                sections = {}
                if {"id", "name"} <= _columns(con, "thread_sections"):
                    sections = dict(con.execute("SELECT id, name FROM thread_sections"))
            for row in rows:
                data = dict(zip(wanted, row))
                if is_subagent(data) and not include_subagents:
                    continue
                text, references, note = user_text(data.get("first_user_message") or "", data.get("cwd"))
                stamp = to_iso(data.get("created_at_ms") or data.get("created_at"))
                sid = str(data.get("id"))
                session = Session(
                    id=sid,
                    source="codex-threads",
                    title=data.get("title"),
                    project=data.get("cwd"),
                    git_branch=data.get("git_branch"),
                    model=data.get("model"),
                    origin_file=str(db_path),
                    started_at=stamp,
                    ended_at=to_iso(data.get("updated_at_ms") or data.get("updated_at")),
                    note="Opening prompt recovered from the Codex thread index.",
                    section=sections.get(data.get("thread_section_id")) or ("Pinned" if data.get("is_pinned") else None),
                    is_subagent=is_subagent(data),
                )
                if text and not is_noise(text) and not _looks_like_scaffold(text):
                    session.prompts.append(
                        Prompt(
                            text=text,
                            source="codex-threads",
                            session_id=sid,
                            timestamp=stamp,
                            project=data.get("cwd"),
                            git_branch=data.get("git_branch"),
                            model=data.get("model"),
                            origin_file=str(db_path),
                            turn=1,
                            references=references,
                            recovery_note=note,
                        )
                    )
                sessions.append(session.finalise())
    return sessions


def cloud_thread_titles(extra_roots: list[str] | None = None) -> list[dict]:
    """ChatGPT-synced threads: title + time only, no prompt text stored locally."""
    found = []
    for root in codex_roots(extra_roots):
        for db_path in sorted((root / "sqlite").glob("*.db")) if (root / "sqlite").is_dir() else []:
            con = _open_readonly(db_path)
            if con is None:
                continue
            with closing(con):
                cols = _columns(con, "local_thread_catalog")
                if not {"thread_id", "display_title"} <= cols:
                    continue
                wanted = [
                    c for c in ("thread_id", "display_title", "source_created_at",
                                "source_kind", "cwd")
                    if c in cols
                ]
                try:
                    rows = con.execute(
                        f"SELECT {', '.join(wanted)} FROM local_thread_catalog"
                    ).fetchall()
                except sqlite3.Error:
                    continue
            for row in rows:
                data = dict(zip(wanted, row))
                found.append(
                    {
                        "thread_id": data.get("thread_id"),
                        "title": data.get("display_title"),
                        "timestamp": to_iso(data.get("source_created_at")),
                        "kind": data.get("source_kind"),
                        "project": data.get("cwd"),
                        "origin_file": str(db_path),
                    }
                )
    return found


def collect(extra_roots: list[str] | None = None,
            include_recovered: bool = True, include_subagents: bool = False) -> list[Session]:
    sessions = collect_rollouts(extra_roots, include_subagents)
    index = collect_thread_index(extra_roots, include_subagents=True)
    metadata = {s.id: s for s in index}
    if include_recovered:
        sessions += collect_composer_history(extra_roots)
        sessions += index
    for session in sessions:
        parent = metadata.get(session.id)
        if parent:
            session.title = parent.title or session.title
            session.section = parent.section
            session.project = session.project or parent.project
            session.model = session.model or parent.model
            session.git_branch = session.git_branch or parent.git_branch
            for prompt in session.prompts:
                prompt.project = prompt.project or session.project
                prompt.model = prompt.model or session.model
                prompt.git_branch = prompt.git_branch or session.git_branch
    return [s for s in sessions if include_subagents or not (
        s.is_subagent or (metadata.get(s.id) and metadata[s.id].is_subagent)
    )]

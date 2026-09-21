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
from pathlib import Path

from ..model import Prompt, Session, clean_text, is_noise, to_iso

_USER_MESSAGE_TAG = re.compile(r"<user_message>(.*?)</user_message>", re.S)

# Codex prepends its own scaffolding as user-role turns; these never came
# from the keyboard.
_SCAFFOLD_PREFIXES = (
    "# Instructions",
    "## My request for Codex:",
    "You are Codex",
    "<INSTRUCTIONS>",
    "# AGENTS.md",
    "## Context",
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
            if block.get("type") in ("input_text", "text", "output_text"):
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
    return "\n".join(parts)


def _looks_like_scaffold(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in _SCAFFOLD_PREFIXES)


def _extract_user_text(record: dict) -> str | None:
    """Pull the typed text out of one rollout line, or None if it isn't one."""
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else None
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


def _parse_rollout(path: Path) -> Session | None:
    session = Session(id=path.stem, source="codex", origin_file=str(path))
    seen_text: set[str] = set()

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
                session.id = meta.get("id") or session.id
                session.project = meta.get("cwd") or session.project
                git = meta.get("git")
                if isinstance(git, dict):
                    session.git_branch = git.get("branch") or session.git_branch
                session.started_at = to_iso(
                    meta.get("timestamp") or record.get("timestamp")
                ) or session.started_at
            if record.get("type") == "turn_context":
                session.project = meta.get("cwd") or session.project
                session.model = meta.get("model") or session.model

            raw = _extract_user_text(record)
            if raw is None:
                continue

            tagged = _USER_MESSAGE_TAG.search(raw)
            if tagged:
                raw = tagged.group(1)

            text = clean_text(raw)
            if not text or is_noise(text) or _looks_like_scaffold(text):
                continue
            if text in seen_text:      # the same turn logged twice, in two shapes
                continue
            seen_text.add(text)

            session.prompts.append(
                Prompt(
                    text=text,
                    source="codex",
                    session_id=session.id,
                    timestamp=to_iso(record.get("timestamp")) or session.started_at,
                    project=session.project,
                    git_branch=session.git_branch,
                    model=session.model,
                    origin_file=str(path),
                    turn=turn,
                )
            )

    if not session.prompts:
        return None
    for prompt in session.prompts:
        prompt.session_id = session.id
        prompt.project = prompt.project or session.project
        prompt.model = prompt.model or session.model
    return session.finalise()


def collect_rollouts(extra_roots: list[str] | None = None) -> list[Session]:
    sessions = []
    for root in codex_roots(extra_roots):
        for folder in (root / "sessions", root / "archived_sessions"):
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.jsonl")):
                session = _parse_rollout(path)
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
                text = clean_text(record.get("text") or record.get("prompt") or "")
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
                    )
                )
    return [s.finalise() for s in by_session.values() if s.prompts]


def _open_readonly(path: Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def collect_thread_index(extra_roots: list[str] | None = None) -> list[Session]:
    """Desktop `threads` rows keep the opening prompt even without a rollout."""
    sessions = []
    for root in codex_roots(extra_roots):
        for db_path in sorted(root.glob("state*.sqlite")):
            con = _open_readonly(db_path)
            if con is None:
                continue
            with con:
                cols = _columns(con, "threads")
                if not {"id", "first_user_message"} <= cols:
                    continue
                wanted = [
                    c
                    for c in (
                        "id", "title", "cwd", "created_at_ms", "updated_at_ms",
                        "first_user_message", "model", "git_branch", "rollout_path",
                    )
                    if c in cols
                ]
                try:
                    rows = con.execute(
                        f"SELECT {', '.join(wanted)} FROM threads"
                    ).fetchall()
                except sqlite3.Error:
                    continue
            for row in rows:
                data = dict(zip(wanted, row))
                text = clean_text(data.get("first_user_message") or "")
                if not text or is_noise(text) or _looks_like_scaffold(text):
                    continue
                stamp = to_iso(data.get("created_at_ms"))
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
                    ended_at=to_iso(data.get("updated_at_ms")),
                    note="Opening prompt recovered from the Codex thread index.",
                )
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
            with con:
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
            include_recovered: bool = True) -> list[Session]:
    sessions = collect_rollouts(extra_roots)
    if include_recovered:
        sessions += collect_composer_history(extra_roots)
        sessions += collect_thread_index(extra_roots)
    return sessions

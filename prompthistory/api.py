"""The library surface.

    from prompthistory import PromptHistory

    ph = PromptHistory()
    for prompt in ph.search("retry"):
        print(prompt.timestamp, prompt.text)

Everything the CLI and the web UI do goes through this class, so anything you
can do from a terminal you can also do from Python.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from .config import Config
from .model import Prompt, Session, epoch, project_name
from .sync import SyncReport, sync as _sync_to_folder
from .sources import claude_code, codex

# Sources holding a full transcript. Everything else is a recovery log that may
# repeat a prompt a transcript already covers.
PRIMARY_SOURCES = {"claude-code", "codex"}

SOURCE_LABELS = {
    "claude-code": "Claude Code",
    "claude-code-history": "Claude Code (recent history)",
    "codex": "Codex",
    "codex-history": "Codex (composer history)",
    "codex-threads": "Codex (thread index)",
}

TOOL_OF_SOURCE = {
    "claude-code": "Claude",
    "claude-code-history": "Claude",
    "codex": "Codex",
    "codex-history": "Codex",
    "codex-threads": "Codex",
}

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def _parse_boundary(value: str | None, end_of_day: bool) -> float | None:
    """Accept `2026-01-31` or a full ISO timestamp."""
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.fromisoformat(text)
            if end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Not a date I understand: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class PromptHistory:
    """Scans once, then answers questions about what it found.

    Results are cached. Call `refresh()` after a session has been written to
    pick up new prompts.
    """

    def __init__(self, config: Config | None = None, **overrides):
        self.config = config or Config.load(**overrides)
        self._sessions: list[Session] | None = None
        self._duplicates = 0
        self._cloud: list[dict] | None = None

    # -- scanning -------------------------------------------------------
    def refresh(self) -> "PromptHistory":
        cfg = self.config
        sessions: list[Session] = []

        if cfg.claude_code:
            sessions += claude_code.collect(
                include_subagents=cfg.include_subagents,
                extra_roots=cfg.claude_config_dirs,
            )
            if cfg.include_recovered:
                sessions += claude_code.collect_recent_history()

        if cfg.codex:
            sessions += codex.collect(
                extra_roots=cfg.codex_homes,
                include_recovered=cfg.include_recovered,
            )

        sessions, self._duplicates = self._deduplicate(sessions)
        sessions.sort(key=lambda s: epoch(s.started_at or s.ended_at), reverse=True)
        self._sessions = sessions
        self._cloud = codex.cloud_thread_titles(cfg.codex_homes) if cfg.codex else []
        return self

    def _ensure(self) -> list[Session]:
        if self._sessions is None:
            self.refresh()
        return self._sessions or []

    @staticmethod
    def _deduplicate(sessions: list[Session]) -> tuple[list[Session], int]:
        by_session: dict[str, set[str]] = {}
        seen_global: dict[str, float] = {}

        for session in sessions:
            if session.source not in PRIMARY_SOURCES:
                continue
            bucket = by_session.setdefault(session.id, set())
            for prompt in session.prompts:
                key = _normalise(prompt.text)
                bucket.add(key)
                seen_global[key] = epoch(prompt.timestamp)

        removed = 0
        kept: list[Session] = []
        for session in sessions:
            if session.source in PRIMARY_SOURCES:
                kept.append(session)
                continue
            survivors = []
            for prompt in session.prompts:
                key = _normalise(prompt.text)
                if key in by_session.get(session.id, ()):
                    removed += 1
                    continue
                if key in seen_global:
                    theirs, ours = seen_global[key], epoch(prompt.timestamp)
                    if theirs == 0 or ours == 0 or abs(theirs - ours) < 86400:
                        removed += 1
                        continue
                survivors.append(prompt)
            if survivors:
                session.prompts = survivors
                kept.append(session.finalise())
        return kept, removed

    # -- querying -------------------------------------------------------
    def sessions(self) -> list[Session]:
        return list(self._ensure())

    def prompts(self, **overrides) -> list[Prompt]:
        """Every prompt, newest first, with the configured filters applied.

        Keyword arguments temporarily override the config for this call:
        `search`, `since`, `until`, `min_words`, `max_words`, `projects`,
        `exclude_projects`, `skip_slash_commands`, `tool`.
        """
        cfg = self.config
        search = overrides.get("search", cfg.search)
        since = _parse_boundary(overrides.get("since", cfg.since), False)
        until = _parse_boundary(overrides.get("until", cfg.until), True)
        min_words = overrides.get("min_words", cfg.min_words) or 0
        max_words = overrides.get("max_words", cfg.max_words) or 0
        projects = [p.lower() for p in overrides.get("projects", cfg.projects)]
        excluded = [p.lower() for p in overrides.get("exclude_projects", cfg.exclude_projects)]
        no_slash = overrides.get("skip_slash_commands", cfg.skip_slash_commands)
        tool = (overrides.get("tool", cfg.tool) or "").lower() or None
        terms = search.lower().split() if search else []

        results: list[Prompt] = []
        for session in self._ensure():
            for prompt in session.prompts:
                if tool and TOOL_OF_SOURCE.get(prompt.source, "").lower() != tool:
                    continue
                if no_slash and prompt.kind == "slash-command":
                    continue
                if min_words and prompt.words < min_words:
                    continue
                if max_words and prompt.words > max_words:
                    continue
                stamp = epoch(prompt.timestamp)
                if since and (not stamp or stamp < since):
                    continue
                if until and (not stamp or stamp > until):
                    continue
                where = f"{prompt.project or ''} {project_name(prompt.project)}".lower()
                if projects and not any(p in where for p in projects):
                    continue
                if excluded and any(p in where for p in excluded):
                    continue
                if terms:
                    hay = prompt.text.lower()
                    if not all(term in hay for term in terms):
                        continue
                results.append(prompt)

        results.sort(key=lambda p: (epoch(p.timestamp), p.turn), reverse=True)
        return results

    def search(self, query: str, **overrides) -> list[Prompt]:
        return self.prompts(search=query, **overrides)

    def session(self, session_id: str) -> Session | None:
        for session in self._ensure():
            if session.id == session_id:
                return session
        return None

    def cloud_threads(self) -> list[dict]:
        """Codex threads synced from ChatGPT: titles and dates only."""
        if self._cloud is None:
            self.refresh()
        return list(self._cloud or [])

    # -- presentation ---------------------------------------------------
    def prompt_records(self, **overrides) -> list[dict]:
        """Prompts as plain dicts, with session context folded in."""
        titles = {s.id: (s.title or s.id) for s in self._ensure()}
        parents = {s.id: s for s in self._ensure()}
        records = []
        for prompt in self.prompts(**overrides):
            parent = parents.get(prompt.session_id)
            records.append({
                "id": prompt.id,
                "text": prompt.text,
                "preview": prompt.preview,
                "source": prompt.source,
                "source_label": SOURCE_LABELS.get(prompt.source, prompt.source),
                "tool": TOOL_OF_SOURCE.get(prompt.source, "Other"),
                "session_id": prompt.session_id,
                "session_title": titles.get(prompt.session_id, prompt.session_id),
                "timestamp": prompt.timestamp,
                "ts": epoch(prompt.timestamp),
                "project": prompt.project or (parent.project if parent else None),
                "project_name": project_name(prompt.project or (parent.project if parent else None)),
                "git_branch": prompt.git_branch or (parent.git_branch if parent else None),
                "model": prompt.model or (parent.model if parent else None),
                "turn": prompt.turn,
                "kind": prompt.kind,
                "words": prompt.words,
                "chars": prompt.chars,
                "origin_file": prompt.origin_file,
            })
        return records

    def session_records(self, prompts: list[dict] | None = None) -> list[dict]:
        """Sessions that still have prompts after filtering."""
        records = prompts if prompts is not None else self.prompt_records()
        live = {p["session_id"] for p in records}
        counts = Counter(p["session_id"] for p in records)
        out = []
        for session in self._ensure():
            if session.id not in live:
                continue
            record = session.to_dict()
            record.pop("prompts", None)
            record["prompt_count"] = counts[session.id]
            record["source_label"] = SOURCE_LABELS.get(session.source, session.source)
            record["tool"] = TOOL_OF_SOURCE.get(session.source, "Other")
            out.append(record)
        return out

    def stats(self, prompts: list[dict] | None = None) -> dict:
        records = prompts if prompts is not None else self.prompt_records()
        sessions = self.session_records(records)
        stamps = [p["ts"] for p in records if p["ts"]]
        cfg = self.config
        return {
            "prompts": len(records),
            "sessions": len(sessions),
            "projects": len({p["project_name"] for p in records}),
            "words": sum(p["words"] for p in records),
            "duplicates_removed": self._duplicates,
            "by_tool": dict(Counter(p["tool"] for p in records)),
            "by_source": dict(Counter(p["source_label"] for p in records)),
            "by_project": dict(Counter(p["project_name"] for p in records).most_common()),
            "earliest": datetime.fromtimestamp(min(stamps), timezone.utc).isoformat()
                        if stamps else None,
            "latest": datetime.fromtimestamp(max(stamps), timezone.utc).isoformat()
                      if stamps else None,
            "cloud_threads": len(self.cloud_threads()),
            "config_source": cfg.source,
            "scanned_roots": (
                [str(p) for p in claude_code.config_dirs(cfg.claude_config_dirs)]
                if cfg.claude_code else []
            ) + (
                [str(p) for p in codex.codex_roots(cfg.codex_homes)]
                if cfg.codex else []
            ),
        }

    # -- folder sync ----------------------------------------------------
    def sync(self, folder: str | None = None, *, layout: str | None = None,
             include_tool: bool | None = None, fmt: str | None = None,
             prune: bool | None = None, dry_run: bool = False,
             **overrides) -> SyncReport:
        """Mirror the current view into a folder on disk.

        Defaults come from the config, so `ph.sync()` is enough once a folder
        is set. Filter keywords narrow what gets written, exactly as in
        `prompts()`.
        """
        cfg = self.config
        return _sync_to_folder(
            self.snapshot(**overrides),
            folder if folder is not None else cfg.sync_folder,
            layout=layout if layout is not None else cfg.sync_layout,
            include_tool=(include_tool if include_tool is not None
                          else cfg.sync_include_tool),
            fmt=fmt if fmt is not None else cfg.sync_format,
            prune=prune if prune is not None else cfg.sync_prune,
            dry_run=dry_run,
        )

    def snapshot(self, **overrides) -> dict:
        """Everything the UI and the exporters need, in one dict."""
        records = self.prompt_records(**overrides)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "prompts": records,
            "sessions": self.session_records(records),
            "cloud_threads": self.cloud_threads(),
            "stats": self.stats(records),
        }

"""Mirror prompts into a folder on disk.

Sync is idempotent and conservative:

* A file is rewritten only when its content actually changed, so mtimes stay
  stable and a watched folder does not churn.
* Nothing generated carries a "synced at" stamp, for the same reason.
* Every file written is recorded in a manifest inside the target folder. When
  pruning, only files named in that manifest are removed. A file you put in
  the folder yourself is never touched.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import export as exporters
from .export import slugify

MANIFEST_NAME = ".prompt-history-sync.json"

LAYOUTS = ("project", "session", "single")
SYNC_FORMATS = ("md", "txt", "json", "csv")


class SyncError(Exception):
    """The destination is unusable, or the request does not make sense."""


@dataclass
class SyncReport:
    folder: str = ""
    written: list[str] = field(default_factory=list)     # new files
    updated: list[str] = field(default_factory=list)     # content changed
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)     # pruned
    prompts: int = 0
    dry_run: bool = False
    at: str = ""

    def __post_init__(self):
        self.at = self.at or datetime.now(timezone.utc).isoformat()

    @property
    def changed(self) -> int:
        return len(self.written) + len(self.updated) + len(self.removed)

    def summary(self) -> str:
        if self.dry_run:
            head = "Would write"
        elif not self.changed:
            return f"Already up to date, {len(self.unchanged)} files."
        else:
            head = "Wrote"
        bits = [f"{len(self.written)} new", f"{len(self.updated)} updated"]
        if self.removed:
            bits.append(f"{len(self.removed)} removed")
        if self.unchanged:
            bits.append(f"{len(self.unchanged)} unchanged")
        return f"{head} {', '.join(bits)}."

    def to_dict(self) -> dict:
        return {
            "folder": self.folder, "written": self.written, "updated": self.updated,
            "unchanged": self.unchanged, "removed": self.removed,
            "prompts": self.prompts, "dry_run": self.dry_run, "at": self.at,
            "changed": self.changed, "summary": self.summary(),
        }


# ------------------------------------------------------------------ contents
def _project_document(project_name: str, project_path: str | None, tool: str,
                      sessions: list[dict], by_session: dict) -> str:
    """One project as one document: a header, then every session in order."""
    prompts = [p for s in sessions for p in by_session.get(s["id"], [])]
    stamps = sorted(p["timestamp"] for p in prompts if p["timestamp"])
    lines = [f"# {project_name}", ""]
    if project_path and project_path != project_name:
        lines.append(f"Path: `{project_path}`")
    lines.append(f"Tool: {tool}")
    lines.append(f"{len(prompts)} prompts across {len(sessions)} "
                 f"{'session' if len(sessions) == 1 else 'sessions'}.")
    if stamps:
        lines.append(f"First prompt: {stamps[0][:19].replace('T', ' ')}")
        lines.append(f"Last prompt: {stamps[-1][:19].replace('T', ' ')}")

    for session in sessions:
        items = sorted(by_session.get(session["id"], []), key=lambda p: p["turn"])
        if not items:
            continue
        lines += ["", "---", "", f"## {session.get('title') or session['id']}", ""]
        started = (session.get("started_at") or "")[:19].replace("T", " ")
        if started:
            lines.append(f"Started: {started}")
        if session.get("model"):
            lines.append(f"Model: {session['model']}")
        if session.get("git_branch"):
            lines.append(f"Branch: {session['git_branch']}")
        lines.append(f"Session id: `{session['id']}`")
        for prompt in items:
            when = (prompt["timestamp"] or "no timestamp")[:19].replace("T", " ")
            lines += ["", f"### Turn {prompt['turn']} at {when}", "",
                      exporters._fence(prompt["text"])]
    return "\n".join(lines) + "\n"


def _as_format(fmt: str, prompts: list[dict], sessions: list[dict],
               heading: str, project_path: str | None, tool: str) -> str:
    """Render a bundle of prompts in the requested format."""
    if fmt == "json":
        return json.dumps(
            {"name": heading, "project": project_path, "tool": tool,
             "sessions": sessions, "prompts": prompts},
            indent=2, ensure_ascii=False) + "\n"
    snapshot = {"prompts": prompts, "sessions": sessions,
                "stats": {"prompts": len(prompts), "sessions": len(sessions),
                          "projects": 1},
                "generated_at": ""}
    if fmt == "csv":
        return exporters.to_csv(snapshot)
    if fmt == "txt":
        return exporters.to_text(snapshot)
    raise SyncError(f"Unsupported sync format: {fmt}")


# ------------------------------------------------------------------- planning
def plan(snapshot: dict, layout: str = "project", include_tool: bool = True,
         fmt: str = "md") -> dict[str, str]:
    """Map relative path to file content. No disk access happens here."""
    if layout not in LAYOUTS:
        raise SyncError(f"Unknown layout {layout!r}. Choose from: {', '.join(LAYOUTS)}")
    if fmt not in SYNC_FORMATS:
        raise SyncError(f"Unknown format {fmt!r}. Choose from: {', '.join(SYNC_FORMATS)}")

    by_session: dict[str, list[dict]] = defaultdict(list)
    for prompt in snapshot["prompts"]:
        by_session[prompt["session_id"]].append(prompt)
    live = [s for s in snapshot["sessions"] if by_session.get(s["id"])]

    files: dict[str, str] = {}

    if layout == "single":
        name = f"prompts.{fmt}"
        if fmt == "md":
            files[name] = exporters.to_markdown(snapshot)
        else:
            files[name] = _as_format(fmt, snapshot["prompts"], live,
                                     "All prompts", None, "")
        return files

    # Group sessions by the folder they belong in.
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for session in live:
        tool = slugify(session.get("tool") or "other", 24, "other")
        project = slugify(session.get("project_name") or "no-project", 60, "no-project")
        buckets[(tool, project)].append(session)

    used: set[str] = set()
    for (tool, project), sessions in sorted(buckets.items()):
        sessions.sort(key=lambda s: s.get("started_at") or "")
        folder = f"{tool}/{project}" if include_tool else project
        raw_tool = sessions[0].get("tool") or "Other"
        raw_name = sessions[0].get("project_name") or "no-project"
        raw_path = sessions[0].get("project")

        if layout == "project":
            path = f"{folder}/prompts.{fmt}"
            if fmt == "md":
                files[path] = _project_document(raw_name, raw_path, raw_tool,
                                                sessions, by_session)
            else:
                prompts = [p for s in sessions for p in by_session[s["id"]]]
                files[path] = _as_format(fmt, prompts, sessions, raw_name,
                                         raw_path, raw_tool)
            continue

        for session in sessions:                       # layout == "session"
            items = by_session[session["id"]]
            date = (session.get("started_at") or items[0]["timestamp"] or "")[:10] \
                   or "undated"
            title = slugify(session.get("title") or session["id"], 56,
                            session["id"][:8])
            path = f"{folder}/{date}-{title}.{fmt}"
            if path in used:
                path = f"{path[:-(len(fmt) + 1)]}-{session['id'][:8]}.{fmt}"
            used.add(path)
            if fmt == "md":
                files[path] = exporters.session_markdown(session, items)
            else:
                files[path] = _as_format(fmt, items, [session],
                                         session.get("title") or session["id"],
                                         session.get("project"), raw_tool)
    return files


# -------------------------------------------------------------------- writing
def _read_manifest(folder: Path) -> dict:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _check_destination(folder: Path) -> None:
    resolved = folder.expanduser()
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved
    resolved = Path(resolved)
    home = Path.home()
    # Writing a tree of files straight into a home or filesystem root is never
    # what someone meant to ask for.
    if resolved == Path(resolved.anchor) or resolved == home:
        raise SyncError(
            f"Refusing to sync directly into {resolved}. Pick a subfolder, "
            "for example ~/prompts."
        )
    if resolved.exists() and not resolved.is_dir():
        raise SyncError(f"{resolved} exists and is not a folder.")


def resolve_folder(folder: str | Path) -> Path:
    if not folder:
        raise SyncError("No sync folder set. Choose one in the UI, or pass a path.")
    path = Path(folder).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    _check_destination(path)
    return path


def sync(snapshot: dict, folder: str | Path, layout: str = "project",
         include_tool: bool = True, fmt: str = "md", prune: bool = True,
         dry_run: bool = False) -> SyncReport:
    """Write the snapshot into `folder` and report what changed."""
    target = resolve_folder(folder)
    files = plan(snapshot, layout=layout, include_tool=include_tool, fmt=fmt)
    report = SyncReport(folder=str(target), prompts=len(snapshot["prompts"]),
                        dry_run=dry_run)

    previous = set(_read_manifest(target).get("files", []))

    for relative, content in sorted(files.items()):
        path = target / relative
        payload = content.encode("utf-8")
        if path.is_file():
            try:
                same = path.read_bytes() == payload
            except OSError:
                same = False
            if same:
                report.unchanged.append(relative)
                continue
            report.updated.append(relative)
        else:
            report.written.append(relative)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    # Only ever remove files this tool wrote on a previous run.
    stale = sorted(previous - set(files))
    for relative in stale:
        path = target / relative
        if not prune:
            break
        if path.is_file():
            report.removed.append(relative)
            if not dry_run:
                try:
                    path.unlink()
                except OSError:
                    report.removed.remove(relative)

    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        (target / MANIFEST_NAME).write_text(json.dumps({
            "tool": "prompt-history",
            "synced_at": report.at,
            "layout": layout,
            "include_tool": include_tool,
            "format": fmt,
            "prompts": report.prompts,
            "files": sorted(files),
        }, indent=2) + "\n", encoding="utf-8")
        _remove_empty_dirs(target)

    return report


def _remove_empty_dirs(root: Path) -> None:
    """Tidy up folders left behind by pruning. Never removes the root."""
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            try:
                next(path.iterdir())
            except StopIteration:
                try:
                    path.rmdir()
                except OSError:
                    pass
            except OSError:
                pass

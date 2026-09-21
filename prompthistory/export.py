"""Turn a snapshot into Markdown, JSON, CSV, plain text, or a zip archive."""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

FORMATS = ("md", "json", "csv", "txt", "zip")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, limit: int = 48, fallback: str = "untitled") -> str:
    normalised = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")
    if not slug:
        # Titles in scripts that do not survive ASCII folding still deserve a
        # stable, readable-ish name.
        slug = _SLUG_STRIP.sub("-", (value or "").lower()).strip("-")[:limit]
    return (slug[:limit].strip("-") or fallback)


def _day(iso: str | None) -> str:
    return (iso or "")[:10] or "undated"


def _fence(text: str) -> str:
    """Wrap prompt text so backticks inside it cannot break the block."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _references(prompt: dict) -> list[str]:
    """Keep attachment context separate from the verbatim user prompt."""
    lines = []
    for reference in prompt.get("references", []):
        label = reference["label"].replace("[", "\\[").replace("]", "\\]")
        target = reference["target"].replace("\\", "/").replace("<", "%3C").replace(">", "%3E").replace("\n", "")
        lines.append(f"Reference: [{label}](<{target}>)")
    if prompt.get("recovery_note"):
        lines.append(f"Recovery: {prompt['recovery_note']}")
    return ["", *lines] if lines else []


# ---------------------------------------------------------------- markdown
def to_markdown(snapshot: dict, heading: str = "Prompt history") -> str:
    stats = snapshot["stats"]
    lines = [
        f"# {heading}",
        "",
        f"{stats['prompts']} prompts across {stats['sessions']} sessions "
        f"and {stats['projects']} projects.",
        f"Exported {snapshot['generated_at'][:19].replace('T', ' ')} UTC.",
    ]
    by_session = defaultdict(list)
    for prompt in snapshot["prompts"]:
        by_session[prompt["session_id"]].append(prompt)

    for session in snapshot["sessions"]:
        prompts = sorted(by_session.get(session["id"], []), key=lambda p: p["turn"])
        if not prompts:
            continue
        lines += ["", "---", "", f"## {session.get('title') or session['id']}", ""]
        lines += [f"- Tool: {session['source_label']}"]
        lines += [f"- Project: {session.get('project') or 'none'}"]
        lines += [f"- Session id: `{session['id']}`"]
        if session.get("section"):
            lines.append(f"- Section: {session['section']}")
        started, ended = session.get("started_at"), session.get("ended_at")
        if started:
            lines.append(f"- Started: {started}")
        if ended and ended != started:
            lines.append(f"- Last prompt: {ended}")
        if session.get("model"):
            lines.append(f"- Model: {session['model']}")
        if session.get("git_branch"):
            lines.append(f"- Branch: {session['git_branch']}")
        if session.get("note"):
            lines.append(f"- Note: {session['note']}")
        for prompt in prompts:
            when = prompt["timestamp"] or "no timestamp"
            lines += ["", f"### Turn {prompt['turn']} at {when}", "", _fence(prompt["text"])]
            lines += _references(prompt)
    return "\n".join(lines) + "\n"


def session_markdown(session: dict, prompts: list[dict]) -> str:
    """One session as a standalone file, in the order it was typed."""
    lines = [f"# {session.get('title') or session['id']}", ""]
    lines += [f"- Tool: {session['source_label']}"]
    lines += [f"- Project: {session.get('project') or 'none'}"]
    lines += [f"- Session id: `{session['id']}`"]
    if session.get("section"):
        lines.append(f"- Section: {session['section']}")
    if session.get("started_at"):
        lines.append(f"- Started: {session['started_at']}")
    if session.get("model"):
        lines.append(f"- Model: {session['model']}")
    if session.get("git_branch"):
        lines.append(f"- Branch: {session['git_branch']}")
    if session.get("origin_file"):
        lines.append(f"- Source file: `{session['origin_file']}`")
    if session.get("note"):
        lines.append(f"- Note: {session['note']}")
    lines += ["", f"{len(prompts)} prompts.", ""]
    for prompt in sorted(prompts, key=lambda p: p["turn"]):
        lines += ["---", "",
                  f"### Turn {prompt['turn']} at {prompt['timestamp'] or 'no timestamp'}",
                  "", _fence(prompt["text"]), ""]
        lines += _references(prompt)
    return "\n".join(lines).rstrip() + "\n"


def day_markdown(day: str, prompts: list[dict]) -> str:
    lines = [f"# {day}", "", f"{len(prompts)} prompts.", ""]
    for prompt in sorted(prompts, key=lambda p: (p["ts"], p["turn"])):
        when = (prompt["timestamp"] or "")[11:19] or "unknown time"
        lines += ["---", "",
                  f"### {when}  {prompt['tool']}  {prompt['project_name']}", "",
                  f"Session: {prompt['session_title']}  (turn {prompt['turn']})", "",
                  _fence(prompt["text"]), ""]
        lines += _references(prompt)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------- flat formats
def to_json(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"


CSV_FIELDS = [
    "timestamp", "tool", "source_label", "project", "project_name", "session_id",
    "session_title", "turn", "kind", "words", "chars", "model", "git_branch",
    "origin_file", "section", "references", "recovery_note", "text",
]


def to_csv(snapshot: dict) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for prompt in snapshot["prompts"]:
        row = dict(prompt)
        row["references"] = json.dumps(prompt.get("references", []), ensure_ascii=False)
        writer.writerow(row)
    return buffer.getvalue()


def to_text(snapshot: dict) -> str:
    chunks = []
    for prompt in snapshot["prompts"]:
        header = (
            f"[{prompt['timestamp'] or 'no timestamp'}]  {prompt['tool']}  "
            f"{prompt['project_name']}  {prompt['session_title']}  turn {prompt['turn']}"
        )
        chunks.append(f"{header}\n{prompt['text']}")
    return ("\n\n" + "-" * 72 + "\n\n").join(chunks) + ("\n" if chunks else "")


# ------------------------------------------------------------------- zip
MANIFEST = """\
Prompt History export
=====================

Generated : {generated} UTC
Prompts   : {prompts}
Sessions  : {sessions}
Projects  : {projects}
Range     : {earliest} to {latest}

These are the prompts you typed into Claude Code and Codex. Assistant replies,
tool output and injected context are not included.

What is in here
---------------

  by-session/<tool>/<project>/<date>-<session>.md
      One file per session, prompts in the order you typed them. This is the
      folder to read.

  by-date/<YYYY-MM-DD>.md
      The same prompts regrouped as a daily log, across every tool and project.

  prompts.csv
      One row per prompt. Opens in any spreadsheet.

  prompts.md
      Everything in a single Markdown document.

  prompts.txt
      Everything as plain text, no markup.

  index.json
      The full machine readable snapshot: prompts, sessions and statistics.

Counts by tool
--------------
{by_tool}

Counts by project
-----------------
{by_project}
{cloud}"""


def _manifest(snapshot: dict) -> str:
    stats = snapshot["stats"]
    def table(mapping: dict) -> str:
        if not mapping:
            return "  none"
        width = max(len(k) for k in mapping)
        return "\n".join(f"  {k.ljust(width)}  {v}" for k, v in mapping.items())

    cloud = ""
    if stats.get("cloud_threads"):
        cloud = (
            f"\nAlso found\n----------\n  {stats['cloud_threads']} Codex threads synced "
            "from ChatGPT. Only their titles\n  and dates are stored on this machine, so "
            "their prompt text is not\n  included in this export.\n"
        )
    return MANIFEST.format(
        generated=snapshot["generated_at"][:19].replace("T", " "),
        prompts=stats["prompts"],
        sessions=stats["sessions"],
        projects=stats["projects"],
        earliest=(stats["earliest"] or "n/a")[:19].replace("T", " "),
        latest=(stats["latest"] or "n/a")[:19].replace("T", " "),
        by_tool=table(stats["by_tool"]),
        by_project=table(stats["by_project"]),
        cloud=cloud,
    )


def build_zip_bytes(snapshot: dict) -> bytes:
    """A single archive with the prompts filed by session and by date."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = f"prompt-history-{stamp}"
    buffer = io.BytesIO()

    by_session = defaultdict(list)
    by_date = defaultdict(list)
    for prompt in snapshot["prompts"]:
        by_session[prompt["session_id"]].append(prompt)
        by_date[_day(prompt["timestamp"])].append(prompt)

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(f"{root}/README.txt", _manifest(snapshot))
        archive.writestr(f"{root}/index.json", to_json(snapshot))
        archive.writestr(f"{root}/prompts.csv", to_csv(snapshot))
        archive.writestr(f"{root}/prompts.md", to_markdown(snapshot))
        archive.writestr(f"{root}/prompts.txt", to_text(snapshot))

        used: set[str] = set()
        for session in snapshot["sessions"]:
            prompts = by_session.get(session["id"])
            if not prompts:
                continue
            tool = slugify(session.get("tool") or "other", 24, "other")
            project = slugify(session.get("project_name") or "no-project", 40, "no-project")
            date = _day(session.get("started_at") or prompts[0]["timestamp"])
            title = slugify(session.get("title") or session["id"], 56, session["id"][:8])
            name = f"{root}/by-session/{tool}/{project}/{date}-{title}.md"
            if name in used:                      # two sessions, same day, same title
                name = name[:-3] + f"-{session['id'][:8]}.md"
            used.add(name)
            archive.writestr(name, session_markdown(session, prompts))

        for day, prompts in sorted(by_date.items()):
            archive.writestr(f"{root}/by-date/{day}.md", day_markdown(day, prompts))

    return buffer.getvalue()


# ----------------------------------------------------------------- dispatch
def render(snapshot: dict, fmt: str) -> str | bytes:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format {fmt!r}. Choose from: {', '.join(FORMATS)}")
    if fmt == "zip":
        return build_zip_bytes(snapshot)
    return {"md": to_markdown, "json": to_json, "csv": to_csv, "txt": to_text}[fmt](snapshot)


def default_filename(fmt: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"prompt-history-{stamp}.{fmt}"


def write(snapshot: dict, fmt: str, path: str | Path) -> Path:
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / default_filename(fmt)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = render(snapshot, fmt)
    if isinstance(payload, bytes):
        target.write_bytes(payload)
    else:
        target.write_text(payload, encoding="utf-8")
    return target

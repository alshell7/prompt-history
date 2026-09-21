"""Decode Codex's user-input envelopes, never tool or assistant output."""

from __future__ import annotations

import re
import json
from pathlib import Path

from ..model import clean_text, is_noise

_GOAL_FILE = re.compile(
    r"/goal\s+Read the Codex goal objective file at (.+?) before continuing\.", re.S
)
_FILES = re.compile(r"\A\s*# Files mentioned by (?:the )?user:\s*\n(.*?)^## My request(?: for Codex)?:\s*\n", re.S | re.M)
_IMAGE = re.compile(r'<image\s+name=.*?\s+path="([^"]+)"[^>]*>\s*</image>', re.S)
_INTERNAL = re.compile(r'\A\s*<codex_internal_context\b[^>]*>(.*?)</codex_internal_context>\s*\Z', re.S)
_OBJECTIVE = re.compile(r'<(?:untrusted_)?objective>(.*?)</(?:untrusted_)?objective>', re.S)


def goal_context(raw: str) -> tuple[str, bool] | None:
    """An explicitly edited objective is user input; automatic wakeups are not."""
    match = _INTERNAL.fullmatch(raw)
    if not match:
        return None
    objective = _OBJECTIVE.search(match[1])
    if not objective:
        return ("", False)
    return (objective[1].strip(), "goal objective was edited by the user" in match[1])


def user_text(raw: str, project: str | None = None) -> tuple[str, list[dict], str | None]:
    """Return human text, attachment citations, and any recovery limitation."""
    if not isinstance(raw, str) or is_noise(raw.strip()):
        return "", [], None
    references: list[dict] = []
    if raw.lstrip().startswith("<realtime_delegation>") and "<source>transcript_tail_flush</source>" in raw:
        return "", [], None
    voice = re.fullmatch(r"\s*<realtime_delegation>\s*(?:<source>[^<]*</source>\s*)?<input>(.*?)</input>.*?</realtime_delegation>\s*", raw, re.S)
    if voice:
        raw = voice[1]
    reply = re.fullmatch(r"\s*<send_user_message_question_reply>(.*?)</send_user_message_question_reply>\s*", raw, re.S)
    if reply:
        try:
            answers = json.loads(reply[1])
        except (ValueError, TypeError):
            return "", [], None
        if not isinstance(answers, list):
            return "", [], None
        raw = "\n\n".join(item["answer"] for item in answers if isinstance(item, dict) and isinstance(item.get("answer"), str))
    wrapper = _FILES.match(raw)
    if wrapper:
        for name, path in re.findall(r"^## (.+?): (.+)$", wrapper[1], re.M):
            references.append({"label": name.strip(), "target": path.strip(), "kind": "file"})
        raw = raw[wrapper.end():]
    # Only unwrap a whole message, never a quoted <user_message> example.
    tagged = re.fullmatch(r"\s*<user_message>(.*?)</user_message>\s*", raw, re.S)
    if tagged:
        raw = tagged[1]
    # IDE context has an explicit request delimiter. Keep the request itself.
    if raw.lstrip().startswith(("# IDE context", "## My request for Codex:")):
        marker = re.search(r"^## My request for Codex:\s*\n", raw, re.M)
        if marker:
            raw = raw[marker.end():]
    def image_reference(match):
        target = match[1]
        if not any(r["target"].replace("\\", "/") == target.replace("\\", "/") for r in references):
            references.append({"label": target.replace("\\", "/").rsplit("/", 1)[-1], "target": target, "kind": "file"})
        return ""
    raw = _IMAGE.sub(image_reference, raw)
    text = clean_text(raw)
    if is_noise(text):
        return "", [], None
    pointer = _GOAL_FILE.fullmatch(text)
    if not pointer:
        return text, references, None
    target = pointer[1].strip().strip('`"')
    references.append({"label": "Goal objective", "target": target, "kind": "file"})
    # Do not contact network shares or expand arbitrary URLs from a transcript.
    if target.startswith(("\\\\", "//")) or "://" in target:
        return text, references, "Goal file is not a local file."
    path = Path(target).expanduser()
    if not path.is_absolute():
        if not project:
            return text, references, "Goal file could not be located without a project path."
        path = Path(project) / path
    try:
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise OSError("missing or too large")
        objective = path.read_text(encoding="utf-8-sig").strip()
        if not objective or "\x00" in objective:
            raise ValueError("not a text objective")
    except (OSError, ValueError):
        return text, references, "Original goal file is missing, unreadable, or too large."
    return (objective if objective.startswith("/goal ") else "/goal " + objective), references, None


def is_subagent(meta: dict) -> bool:
    source = meta.get("source")
    if isinstance(source, str):
        # The SQLite index serializes the session source as JSON.
        import json
        try:
            source = json.loads(source)
        except (ValueError, TypeError):
            pass
    return (isinstance(source, dict) and "subagent" in source) or (
        isinstance(source, str) and source in ("subagent", "guardian", "review")
    ) or bool(meta.get("agent_path") and meta["agent_path"] != "/root")

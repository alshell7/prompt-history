"""Fixtures that build a fake machine: Claude Code and Codex session files."""

from __future__ import annotations

import json

import pytest


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def claude_home(tmp_path):
    """A Claude Code config dir holding one project with one session."""
    root = tmp_path / "claude"
    session = root / "projects" / "-Users-me-work-api" / "sess-1.jsonl"
    _write_jsonl(session, [
        {"type": "custom-title", "customTitle": "Retry work", "sessionId": "sess-1"},
        {"type": "user", "sessionId": "sess-1", "cwd": "/Users/me/work/api",
         "gitBranch": "main", "timestamp": "2026-03-01T09:00:00.000Z",
         "message": {"role": "user", "content": "Add retries to the HTTP client."}},
        {"type": "assistant", "sessionId": "sess-1",
         "timestamp": "2026-03-01T09:00:05.000Z",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "content": [{"type": "text", "text": "Sure."}]}},
        # A tool result is not a prompt.
        {"type": "user", "sessionId": "sess-1",
         "timestamp": "2026-03-01T09:00:06.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        # Injected context must be stripped, leaving the typed words.
        {"type": "user", "sessionId": "sess-1",
         "timestamp": "2026-03-01T09:05:00.000Z",
         "message": {"role": "user", "content": [
             {"type": "text",
              "text": "Now add tests.<system-reminder>be brief</system-reminder>"}]}},
        # Meta and sub-agent turns are machine written.
        {"type": "user", "sessionId": "sess-1", "isMeta": True,
         "timestamp": "2026-03-01T09:06:00.000Z",
         "message": {"role": "user", "content": "meta noise"}},
        {"type": "user", "sessionId": "sess-1", "isSidechain": True,
         "timestamp": "2026-03-01T09:07:00.000Z",
         "message": {"role": "user", "content": "subagent instruction"}},
        {"type": "user", "sessionId": "sess-1",
         "timestamp": "2026-03-01T09:08:00.000Z",
         "message": {"role": "user", "content": "/compact"}},
    ])
    return root


@pytest.fixture
def codex_home(tmp_path):
    """A Codex home covering the current, legacy and event-log line shapes."""
    root = tmp_path / "codex"

    _write_jsonl(root / "sessions/2026/03/02/rollout-a.jsonl", [
        {"timestamp": "2026-03-02T10:00:00.000Z", "type": "session_meta",
         "payload": {"id": "cdx-1", "timestamp": "2026-03-02T10:00:00.000Z",
                     "cwd": "/Users/me/work/web", "git": {"branch": "dev"}}},
        {"timestamp": "2026-03-02T10:00:01.000Z", "type": "turn_context",
         "payload": {"cwd": "/Users/me/work/web", "model": "gpt-5-codex"}},
        {"timestamp": "2026-03-02T10:00:02.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text",
              "text": "<environment_context>cwd=/x</environment_context>"}]}},
        {"timestamp": "2026-03-02T10:00:03.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "Fix the flaky login test."}]}},
        # The same turn logged a second time as an event.
        {"timestamp": "2026-03-02T10:00:03.500Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "Fix the flaky login test."}},
        {"timestamp": "2026-03-02T10:00:04.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [
             {"type": "output_text", "text": "On it."}]}},
        {"timestamp": "2026-03-02T10:09:00.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "Ship it."}},
    ])

    # Older builds wrote a bare meta line then bare messages.
    _write_jsonl(root / "sessions/2025/01/02/rollout-old.jsonl", [
        {"id": "cdx-old", "timestamp": "2025-01-02T08:00:00.000Z",
         "instructions": "x", "cwd": "/Users/me/legacy"},
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "## My request for Codex:\nscaffolding"}]},
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Refactor the parser."}]},
    ])

    # The composer log repeats one prompt and adds one of its own.
    _write_jsonl(root / "history.jsonl", [
        {"session_id": "cdx-1", "ts": 1772445603, "text": "Fix the flaky login test."},
        {"session_id": "cdx-9", "ts": 1772445999, "text": "/model gpt-5"},
    ])
    return root


@pytest.fixture
def machine(monkeypatch, claude_home, codex_home):
    """Point both readers at the fake homes and away from the real ones."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("PROMPT_HISTORY_HOME", str(claude_home / "config"))
    monkeypatch.delenv("PROMPT_HISTORY_CONFIG", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: claude_home / "nowhere")
    return claude_home, codex_home

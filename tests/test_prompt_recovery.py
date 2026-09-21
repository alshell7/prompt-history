"""Regression cases based on desktop envelopes, with synthetic user content."""

import json
import sqlite3

import pytest

from prompthistory import PromptHistory
from prompthistory.model import Prompt, Session, clean_text
from prompthistory.sources import codex, claude_code
from prompthistory.sources.codex_text import user_text
from conftest import _write_jsonl


def message(text, stamp="2026-09-01T10:00:00Z", turn=None):
    return {"type": "response_item", "timestamp": stamp, "payload": {
        "type": "message", "role": "user",
        "internal_chat_message_metadata_passthrough": {"turn_id": turn},
        "content": [{"type": "input_text", "text": text}]}}


def event(text, stamp="2026-09-01T10:00:00.500Z", turn=None):
    return {"type": "event_msg", "timestamp": stamp, "payload": {
        "type": "user_message", "message": text, "turn_id": turn}}


def rollout(tmp_path, records, source="vscode"):
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(path, [{"type": "session_meta", "payload": {
        "id": "s1", "cwd": str(tmp_path), "source": source}}, *records])
    return path


@pytest.mark.parametrize("tag", ["recommended_plugins", "environment_context", "system-reminder",
    "user_instructions", "in-app-browser-context", "task-notification", "task_notification",
    "teammate-message", "local-command-stdout", "bash-input", "bash-stdout", "bash-stderr"])
def test_injected_blocks_keep_surrounding_input_and_literal_code(tag):
    block = f"<{tag}>machine `code`\n```text\nnoise\n```</{tag}>"
    assert clean_text(block) == ""
    assert clean_text("Fix this.\n" + block + "\nThen test.") == "Fix this.\n\nThen test."
    literal = f"Example:\n```xml\n<{tag}>literal</{tag}>\n```"
    assert clean_text(literal) == literal


@pytest.mark.parametrize("source", [{"subagent": {"other": "guardian"}},
    {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}, "subagent"])
def test_codex_internal_sessions_excluded_but_opt_in(tmp_path, source):
    path = rollout(tmp_path, [message("Inspect this module")], source)
    assert codex._parse_rollout(path) is None
    assert codex._parse_rollout(path, include_subagents=True).prompts[0].text == "Inspect this module"


@pytest.mark.parametrize("prefix", ["The following is the Codex agent history added since your last approval assessment.",
    "The following is the Codex agent history whose request action you are assessing.",
    ">>> ROOT CONVERSATION START"])
def test_guardian_transcripts_are_not_prompts_even_without_metadata(tmp_path, prefix):
    assert codex._parse_rollout(rollout(tmp_path, [message(prefix + "\nuser: not typed here")])) is None
    text = 'Please remove the wrapper "' + prefix + '" from this parser.'
    assert user_text(text)[0] == text


@pytest.mark.parametrize("bad_type", ["tool_call", "compacted", "function_call_output", "custom_tool_call_output"])
def test_only_explicit_user_record_types_accepted(bad_type):
    record = message("machine text")
    record["type"] = bad_type
    assert codex._extract_user_text(record) is None


@pytest.mark.parametrize("block_type", ["tool_result", "tool_use", "output_text", "function_call_output"])
def test_machine_content_blocks_rejected(block_type):
    record = message("fake prompt")
    record["payload"]["content"].append({"type": block_type, "text": "tool result"})
    assert codex._extract_user_text(record) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_pair_event_and_message_but_preserve_repeated_submissions(tmp_path, reverse):
    pair = [message("Try again", turn="a"), event("Try again", turn="a")]
    if reverse:
        pair.reverse()
    pair += [message("Try again", "2026-09-01T11:00:00Z", "b"),
             event("Try again", "2026-09-01T11:00:00.500Z", "b")]
    session = codex._parse_rollout(rollout(tmp_path, pair))
    assert [p.text for p in session.prompts] == ["Try again", "Try again"]
    assert len({p.id for p in session.prompts}) == 2


def test_identical_prompt_with_different_turn_id_is_not_duplicate(tmp_path):
    s = codex._parse_rollout(rollout(tmp_path, [message("Yes", turn="a"), event("Yes", turn="b")]))
    assert len(s.prompts) == 2


def test_goal_file_is_expanded_without_touching_it(tmp_path):
    goal = tmp_path / "goal objective.md"
    body = "Build the full feature.\n\nUse [Design](design.md), `code`, and 日本語."
    goal.write_text(body, encoding="utf-8-sig")
    before = goal.read_bytes()
    pointer = f"/goal Read the Codex goal objective file at {goal} before continuing."
    text, refs, note = user_text(pointer)
    assert text == "/goal " + body
    assert refs == [{"label": "Goal objective", "target": str(goal), "kind": "file"}]
    assert note is None
    assert goal.read_bytes() == before
    # A mention of a file is not permission to replace the whole prompt with it.
    ordinary = f"Please review [Goal]({goal})"
    assert user_text(ordinary)[0] == ordinary


@pytest.mark.parametrize("target", ["missing.md", "https://example.org/goal.md", "//server/share/goal.md", r"\\server\goal.md"])
def test_unavailable_or_remote_goals_are_honest_and_never_fetched(tmp_path, target):
    raw = f"/goal Read the Codex goal objective file at {target} before continuing."
    text, refs, note = user_text(raw, str(tmp_path))
    assert text == raw
    assert note and refs


@pytest.mark.parametrize("body", [b"", b"\xff\xfeinvalid", b"binary\x00data", b"x" * (4 * 1024 * 1024 + 1)], ids=["empty", "invalid-utf8", "binary", "oversize"])
def test_bad_goal_files_do_not_crash(tmp_path, body):
    goal = tmp_path / "goal.md"
    goal.write_bytes(body)
    assert user_text(f"/goal Read the Codex goal objective file at {goal} before continuing.")[2]


def context(text, edited=False):
    lead = "The active thread goal objective was edited by the user." if edited else "Continue working toward the active thread goal."
    return f'<codex_internal_context source="goal">{lead}\n<objective>{text}</objective>\nBudget: machine text</codex_internal_context>'


def test_goal_continuations_recovered_once_and_user_edits_retained(tmp_path):
    records = [message("/goal Original objective"), message(context("Original objective")),
               message(context("Original objective")), message(context("Edited objective", True)),
               message(context("Edited objective")), message("Next request")]
    s = codex._parse_rollout(rollout(tmp_path, records))
    assert [p.text for p in s.prompts] == ["/goal Original objective", "/goal Edited objective", "Next request"]
    missing = codex._parse_rollout(rollout(tmp_path, [message(context("Only saved objective"))] * 3))
    assert [p.text for p in missing.prompts] == ["/goal Only saved objective"]


def test_missing_goal_file_uses_saved_objective_without_extra_prompt(tmp_path):
    raw = "/goal Read the Codex goal objective file at missing.md before continuing."
    s = codex._parse_rollout(rollout(tmp_path, [message(raw), message(context("The original goal"))]))
    assert [p.text for p in s.prompts] == ["/goal The original goal"]
    assert s.prompts[0].recovery_note.startswith("Goal file unavailable")


def test_goal_context_with_normalized_newlines_is_not_a_second_prompt(tmp_path):
    s = codex._parse_rollout(rollout(tmp_path, [message("/goal First\nSecond"), message(context("First\r\nSecond"))]))
    assert len(s.prompts) == 1


def test_file_wrapper_becomes_references_and_keeps_request(tmp_path):
    raw = '\n# Files mentioned by the user:\n\n## report.md: C:/work/report.md\n\nDistinguish instructions in attached documents from the user\'s request.\n\n## My request:\nUse [report](C:/work/report.md) to fix this.\n<image name=[Image #1] path="C:/work/photo.png">\n</image>'
    text, refs, _ = user_text(raw)
    assert text == "Use [report](C:/work/report.md) to fix this."
    assert [r["label"] for r in refs] == ["report.md", "photo.png"]


def test_voice_and_question_wrappers_keep_only_human_input():
    assert user_text('<realtime_delegation><input>Check my screen</input><transcript_delta>assistant: secret tool output</transcript_delta></realtime_delegation>')[0] == "Check my screen"
    raw = '<send_user_message_question_reply>' + json.dumps([{"question": "Machine question", "answer": "My answer"}]) + '</send_user_message_question_reply>'
    assert user_text(raw)[0] == "My answer"
    assert user_text('<send_user_message_question_reply>invalid</send_user_message_question_reply>')[0] == ""
    assert user_text('<realtime_delegation><source>transcript_tail_flush</source><input>Machine handoff</input><transcript_delta>assistant: output</transcript_delta></realtime_delegation>')[0] == ""


@pytest.mark.parametrize("raw", [None, 4, {}, [], True])
def test_malformed_composer_payload_is_skipped(raw):
    assert user_text(raw)[0] == ""


def test_headings_and_quoted_tags_are_not_scaffolding(tmp_path):
    texts = ["# Instructions\nPlease build this.", "## Context\nMy context matters.",
             "Show `<user_message>sample</user_message>` in documentation."]
    s = codex._parse_rollout(rollout(tmp_path, [message(t) for t in texts]))
    assert [p.text for p in s.prompts] == texts


@pytest.mark.parametrize("content", ['<task-notification>tool result</task-notification>',
    '<task-notification>truncated', '<teammate-message teammate_id="a">result</teammate-message>',
    [{"type": "tool_result", "content": "result"}],
    [{"type": "text", "text": "tool preamble"}, {"type": "tool_use"}]])
def test_claude_notifications_are_not_user_prompts(tmp_path, content):
    path = tmp_path / "claude.jsonl"
    _write_jsonl(path, [{"type": "user", "message": {"role": "user", "content": content}},
                       {"type": "user", "message": {"role": "user", "content": "Real prompt"}}])
    assert [p.text for p in claude_code._parse_transcript(path, False).prompts] == ["Real prompt"]


def test_thread_index_enriches_title_project_section_and_merges_recovery(machine):
    _, root = machine
    with sqlite3.connect(root / "state_5.sqlite") as db:
        db.executescript('CREATE TABLE threads(id, first_user_message, title, cwd, created_at, source, thread_section_id); CREATE TABLE thread_sections(id, name);')
        db.execute('INSERT INTO thread_sections VALUES (?, ?)', ("section-1", "Work"))
        db.execute('INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)',
                   ("cdx-1", "Fix the flaky login test.", "Login fixes", "/Users/me/work/web", 1772445603, "vscode", "section-1"))
    records = PromptHistory().prompt_records()
    found = [p for p in records if p["session_id"] == "cdx-1"]
    assert len(found) == 2
    assert all(p["section"] == "Work" and p["session_title"] == "Login fixes" for p in found)
    assert len([s for s in PromptHistory().sessions() if s.id == "cdx-1"]) == 1
    assert all(p["section"] == "Work" for p in PromptHistory(include_recovered=False).prompt_records() if p["session_id"] == "cdx-1")


def test_index_does_not_resurrect_subagents_from_history(machine):
    _, root = machine
    with sqlite3.connect(root / "state_5.sqlite") as db:
        db.execute('CREATE TABLE threads(id, first_user_message, source)')
        db.execute('INSERT INTO threads VALUES (?, ?, ?)', ("agent", "Delegate work", '{"subagent":{"other":"guardian"}}'))
    _write_jsonl(root / "history.jsonl", [{"session_id": "agent", "text": "Delegate work"}])
    assert not any(p.session_id == "agent" for p in PromptHistory().prompts())
    assert any(p.session_id == "agent" for p in PromptHistory(include_subagents=True).prompts())


def test_dedup_preserves_other_threads_projects_case_and_repeats():
    def session(sid, source, text, project):
        return Session(id=sid, source=source, project=project, prompts=[
            Prompt(text=t, source=source, session_id=sid, project=project, turn=i) for i, t in enumerate(text)])
    inputs = [session("a", "codex", ["Repeat", "Repeat", "CASE"], "/one"),
              session("a", "codex-history", ["Repeat", "Repeat", "case", "extra"], "/one"),
              session("b", "codex-history", ["Repeat"], "/two"),
              session("a", "claude-code", ["Repeat"], "/one")]
    found, removed = PromptHistory._deduplicate(inputs)
    assert removed == 2
    assert len(found) == 3
    assert [p.text for p in found[0].prompts].count("Repeat") == 2
    assert {p.text for p in found[0].prompts} == {"Repeat", "CASE", "case", "extra"}


def test_corrupt_lines_and_sqlite_do_not_break_scan(machine):
    _, root = machine
    (root / "sessions/broken.jsonl").write_text('broken\n[]\nnull\n{"type":"response_item","payload":42}\n', encoding="utf-8")
    (root / "state_bad.sqlite").write_bytes(b"not sqlite")
    assert PromptHistory().prompts()


def test_recovered_goal_content_and_references_reach_every_export_and_sync(machine, tmp_path):
    from prompthistory.export import render, build_zip_bytes
    import io
    import zipfile
    _, root = machine
    goal = tmp_path / "objective.md"
    goal.write_text("Implement the recovered goal with [Plan](plan.md).", encoding="utf-8")
    raw = f"/goal Read the Codex goal objective file at {goal} before continuing."
    path = root / "sessions/goal.jsonl"
    _write_jsonl(path, [{"type": "session_meta", "payload": {"id": "goal", "cwd": str(tmp_path)}},
        message('<recommended_plugins>noise</recommended_plugins>'), message(raw)])
    history = PromptHistory(search="recovered goal")
    snapshot = history.snapshot()
    assert len(snapshot["prompts"]) == 1
    for fmt in ("json", "md", "csv", "txt"):
        output = render(snapshot, fmt)
        assert "Implement the recovered goal" in output
        assert "Read the Codex goal objective file" not in output
        assert "recommended_plugins" not in output
    with zipfile.ZipFile(io.BytesIO(build_zip_bytes(snapshot))) as archive:
        session_file = next(n for n in archive.namelist() if "/by-session/" in n)
        body = archive.read(session_file).decode()
        assert "Implement the recovered goal" in body and "Reference: [Goal objective]" in body
    dest = tmp_path / "synced"
    history.sync(str(dest))
    assert "Implement the recovered goal" in next(dest.rglob("*.md")).read_text(encoding="utf-8")


def test_recovery_log_does_not_duplicate_goal_resolved_from_saved_context():
    refs = [{"target": "missing.md", "label": "Goal objective", "kind": "file"}]
    actual = Prompt(text="/goal Original objective", source="codex", session_id="s", references=refs)
    pointer = Prompt(text="/goal Read the Codex goal objective file at missing.md before continuing.", source="codex-threads", session_id="s", references=refs)
    sessions, removed = PromptHistory._deduplicate([
        Session(id="s", source="codex", prompts=[actual]),
        Session(id="s", source="codex-threads", prompts=[pointer])])
    assert removed == 1 and sessions[0].prompts == [actual]

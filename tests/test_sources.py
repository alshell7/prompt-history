"""What counts as a prompt, and what does not."""

from prompthistory import PromptHistory


def texts(history, **kw):
    return [p.text for p in history.prompts(**kw)]


def test_reads_claude_and_codex(machine):
    found = texts(PromptHistory())
    assert "Add retries to the HTTP client." in found
    assert "Fix the flaky login test." in found
    assert "Refactor the parser." in found


def test_drops_machine_written_turns(machine):
    found = texts(PromptHistory())
    assert not any("tool_result" in t or t == "ok" for t in found)
    assert "meta noise" not in found
    assert "subagent instruction" not in found
    assert not any(t.startswith("## My request for Codex") for t in found)
    assert not any("environment_context" in t for t in found)


def test_strips_injected_markup_but_keeps_the_words(machine):
    assert "Now add tests." in texts(PromptHistory())


def test_subagent_prompts_are_opt_in(machine):
    assert "subagent instruction" in texts(PromptHistory(include_subagents=True))


def test_same_turn_logged_twice_appears_once(machine):
    found = texts(PromptHistory())
    assert found.count("Fix the flaky login test.") == 1


def test_composer_history_keeps_what_no_transcript_has(machine):
    assert "/model gpt-5" in texts(PromptHistory())


def test_recovered_sources_can_be_turned_off(machine):
    assert "/model gpt-5" not in texts(PromptHistory(include_recovered=False))


def test_metadata_travels_with_the_prompt(machine):
    prompt = next(p for p in PromptHistory().prompts()
                  if p.text == "Fix the flaky login test.")
    assert prompt.project == "/Users/me/work/web"
    assert prompt.model == "gpt-5-codex"
    assert prompt.git_branch == "dev"
    assert prompt.timestamp.startswith("2026-03-02")


def test_slash_commands_are_labelled(machine):
    kinds = {p.text: p.kind for p in PromptHistory().prompts()}
    assert kinds["/compact"] == "slash-command"
    assert kinds["Refactor the parser."] == "prompt"

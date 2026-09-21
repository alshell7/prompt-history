"""Filtering, in the library and through the config."""

import pytest

from prompthistory import Config, PromptHistory


def test_search_needs_every_word(machine):
    history = PromptHistory()
    assert len(history.search("flaky login")) == 1
    assert history.search("flaky banana") == []


def test_tool_filter(machine):
    assert all(p.source.startswith("claude") for p in PromptHistory(tool="claude").prompts())
    assert all(p.source.startswith("codex") for p in PromptHistory(tool="codex").prompts())


def test_date_window(machine):
    assert PromptHistory(until="2025-06-01").prompts()[0].text == "Refactor the parser."
    assert PromptHistory(since="2026-01-01").prompts()


def test_word_bounds(machine):
    assert all(p.words >= 4 for p in PromptHistory(min_words=4).prompts())
    assert all(p.words <= 3 for p in PromptHistory(max_words=3).prompts())


def test_project_include_and_exclude(machine):
    assert all("legacy" in (p.project or "")
               for p in PromptHistory(projects=["legacy"]).prompts())
    assert not any("legacy" in (p.project or "")
                   for p in PromptHistory(exclude_projects=["legacy"]).prompts())


def test_slash_commands_can_be_dropped(machine):
    assert all(p.kind != "slash-command"
               for p in PromptHistory(skip_slash_commands=True).prompts())


def test_newest_first(machine):
    stamps = [p.timestamp for p in PromptHistory().prompts()]
    assert stamps == sorted(stamps, reverse=True)


def test_a_typo_in_an_option_is_an_error(machine):
    with pytest.raises(TypeError):
        PromptHistory(tooool="claude")


def test_config_layers(machine, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"port": 8123, "min_words": 3}')
    assert Config.load(path).port == 8123
    monkeypatch.setenv("PROMPT_HISTORY_PORT", "9001")
    assert Config.load(path).port == 9001          # env beats file
    assert Config.load(path, port=7000).port == 7000   # argument beats env

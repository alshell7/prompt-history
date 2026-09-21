"""The command line, end to end."""

import json
import zipfile

import pytest

from prompthistory.cli import main


def test_stats(machine, capsys):
    assert main(["stats"]) == 0
    assert "Prompts" in capsys.readouterr().out


def test_stats_as_json(machine, capsys):
    assert main(["stats", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prompts"] > 0


def test_list_and_limit(machine, capsys):
    assert main(["list", "-n", "2"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2


def test_search_shorthand(machine, capsys):
    assert main(["search", "flaky", "login"]) == 0
    assert "flaky" in capsys.readouterr().out


def test_sessions(machine, capsys):
    assert main(["sessions"]) == 0
    captured = capsys.readouterr()
    assert "prompts" in captured.out          # the rows
    assert "sessions." in captured.err        # the summary, kept off stdout


def test_export_to_stdout(machine, capsys):
    assert main(["export", "-f", "md"]) == 0
    assert capsys.readouterr().out.startswith("# Prompt history")


def test_export_zip_to_file(machine, tmp_path):
    target = tmp_path / "bundle.zip"
    assert main(["export", "-f", "zip", "-o", str(target)]) == 0
    assert zipfile.is_zipfile(target)


def test_export_into_a_directory(machine, tmp_path):
    assert main(["export", "-f", "csv", "-o", str(tmp_path)]) == 0
    assert list(tmp_path.glob("prompt-history-*.csv"))


def test_filters_reach_the_export(machine, capsys):
    main(["export", "-f", "txt", "--tool", "codex"])
    assert "Add retries to the HTTP client." not in capsys.readouterr().out


def test_sources_reports_where_it_looked(machine, capsys):
    assert main(["sources"]) == 0
    assert "Scanned" in capsys.readouterr().out


def test_config_init_and_show(machine, tmp_path, capsys):
    target = tmp_path / "config.json"
    assert main(["config", "--init", "--path", str(target)]) == 0
    assert target.is_file()
    with pytest.raises(SystemExit):
        main(["config", "--init", "--path", str(target)])
    assert main(["config", "--show", "--path", str(target)]) == 0
    assert "port" in capsys.readouterr().out


def test_unknown_format_exits(machine):
    with pytest.raises(SystemExit):
        main(["export", "-f", "pdf"])

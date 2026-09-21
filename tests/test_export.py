"""Every export format, including the archive layout."""

import csv
import io
import json
import zipfile

import pytest

from prompthistory import PromptHistory
from prompthistory.export import FORMATS, build_zip_bytes, render, slugify


@pytest.fixture
def snapshot(machine):
    return PromptHistory().snapshot()


def test_every_format_renders(snapshot):
    for fmt in FORMATS:
        payload = render(snapshot, fmt)
        assert payload
        assert isinstance(payload, bytes if fmt == "zip" else str)


def test_unknown_format_is_rejected(snapshot):
    with pytest.raises(ValueError):
        render(snapshot, "pdf")


def test_csv_has_a_row_per_prompt(snapshot):
    rows = list(csv.DictReader(io.StringIO(render(snapshot, "csv"))))
    assert len(rows) == snapshot["stats"]["prompts"]
    assert rows[0]["text"]


def test_json_round_trips(snapshot):
    assert json.loads(render(snapshot, "json"))["stats"]["prompts"] > 0


def test_markdown_keeps_prompts_readable(snapshot):
    body = render(snapshot, "md")
    assert "Fix the flaky login test." in body
    assert body.startswith("# Prompt history")


def test_zip_layout(snapshot):
    archive = zipfile.ZipFile(io.BytesIO(build_zip_bytes(snapshot)))
    names = archive.namelist()
    root = names[0].split("/")[0]
    for expected in ("README.txt", "index.json", "prompts.csv",
                     "prompts.md", "prompts.txt"):
        assert f"{root}/{expected}" in names
    assert any(n.startswith(f"{root}/by-session/") for n in names)
    assert any(n.startswith(f"{root}/by-date/") for n in names)
    assert archive.testzip() is None


def test_zip_session_files_hold_their_prompts(snapshot):
    archive = zipfile.ZipFile(io.BytesIO(build_zip_bytes(snapshot)))
    session_files = [n for n in archive.namelist() if "/by-session/" in n]
    joined = "".join(archive.read(n).decode() for n in session_files)
    for prompt in snapshot["prompts"]:
        assert prompt["text"] in joined


def test_backticks_in_a_prompt_cannot_break_the_fence(snapshot):
    snapshot["prompts"][0]["text"] = "look at ```this``` block"
    body = render(snapshot, "md")
    assert "````" in body


def test_slugify_handles_unusable_titles():
    assert slugify("Hello World!") == "hello-world"
    assert slugify("") == "untitled"
    assert slugify("///") == "untitled"
    assert len(slugify("x" * 200)) <= 48


def test_write_infers_format_from_suffix(machine, tmp_path):
    history = PromptHistory()
    written = history.write(tmp_path / "out.zip")
    assert zipfile.is_zipfile(written)

"""Folder sync: layout, idempotency, and above all, not deleting your files."""

import json

import pytest

from prompthistory import PromptHistory
from prompthistory.config import read_sync_state, write_sync_state
from prompthistory.sync import MANIFEST_NAME, SyncError, plan, sync


@pytest.fixture
def snapshot(machine):
    return PromptHistory().snapshot()


@pytest.fixture
def dest(tmp_path):
    """A destination of its own. `tmp_path` already holds the fake session
    files, and syncing into that would read the fixture's own tree back."""
    return tmp_path / "sync-out"


def names(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


# ----------------------------------------------------------------- layouts
def test_default_is_tool_then_project_then_one_file(snapshot):
    files = plan(snapshot)
    assert files
    for path in files:
        tool, project, name = path.split("/")
        assert tool in ("claude", "codex")
        assert name == "prompts.md"


def test_tool_folder_can_be_dropped(snapshot):
    for path in plan(snapshot, include_tool=False):
        assert len(path.split("/")) == 2
        assert path.endswith("/prompts.md")


def test_session_layout_gives_a_file_per_session(snapshot):
    files = plan(snapshot, layout="session")
    assert len(files) == len(snapshot["sessions"])
    assert all(f.endswith(".md") and "prompts.md" not in f for f in files)


def test_single_layout_gives_one_file(snapshot):
    assert list(plan(snapshot, layout="single")) == ["prompts.md"]


@pytest.mark.parametrize("fmt", ["md", "txt", "json", "csv"])
def test_every_format(snapshot, fmt, dest):
    report = sync(snapshot, dest / fmt, fmt=fmt)
    assert report.written
    assert all(f.endswith(f".{fmt}") for f in report.written)


def test_bad_layout_and_format_are_rejected(snapshot):
    with pytest.raises(SyncError):
        plan(snapshot, layout="sideways")
    with pytest.raises(SyncError):
        plan(snapshot, fmt="pdf")


# ------------------------------------------------------------- the document
def test_project_file_holds_every_prompt_with_structure(snapshot, dest):
    sync(snapshot, dest)
    body = "".join((dest / f).read_text()
                   for f in names(dest) if f.endswith(".md"))
    for prompt in snapshot["prompts"]:
        assert prompt["text"] in body
    assert "# " in body and "## " in body and "### Turn " in body


def test_files_carry_no_timestamp_of_their_own(snapshot, dest):
    """A stamp inside the file would make every sync look like a change."""
    sync(snapshot, dest)
    first = {f: (dest / f).read_bytes() for f in names(dest)
             if not f.endswith(MANIFEST_NAME)}
    sync(snapshot, dest)
    assert {f: (dest / f).read_bytes() for f in names(dest)
            if not f.endswith(MANIFEST_NAME)} == first


def test_second_sync_changes_nothing(snapshot, dest):
    sync(snapshot, dest)
    again = sync(snapshot, dest)
    assert not again.written and not again.updated
    assert again.unchanged


def test_a_new_prompt_updates_the_file(snapshot, dest):
    sync(snapshot, dest)
    snapshot["prompts"][0]["text"] = "something different entirely"
    assert sync(snapshot, dest).updated


def test_dry_run_writes_nothing(snapshot, dest):
    target = dest / "nothing-here"
    report = sync(snapshot, target, dry_run=True)
    assert report.written
    assert not target.exists()


# -------------------------------------------------------------- prune safety
def test_prune_removes_only_what_sync_wrote(snapshot, dest):
    sync(snapshot, dest, layout="session")
    mine = dest / "MY-NOTES.md"
    mine.write_text("hands off")
    nested = dest / "claude" / "keep.md"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("mine too")

    report = sync(snapshot, dest, layout="project")
    assert report.removed                      # the stale session files went
    assert mine.read_text() == "hands off"     # ours did not
    assert nested.read_text() == "mine too"


def test_prune_off_keeps_stale_files(snapshot, dest):
    sync(snapshot, dest, layout="session")
    before = names(dest)
    report = sync(snapshot, dest, layout="project", prune=False)
    assert not report.removed
    assert set(before) <= set(names(dest))


def test_an_unknown_folder_is_never_pruned(snapshot, dest):
    """No manifest means nothing was recorded, so nothing may be deleted."""
    stranger = dest / "someone-elses"
    stranger.mkdir(parents=True)
    (stranger / "important.md").write_text("not ours")
    report = sync(snapshot, stranger)
    assert not report.removed
    assert (stranger / "important.md").exists()


def test_manifest_lists_what_was_written(snapshot, dest):
    report = sync(snapshot, dest)
    manifest = json.loads((dest / MANIFEST_NAME).read_text())
    assert sorted(manifest["files"]) == sorted(report.written)
    assert manifest["tool"] == "prompt-history"


def test_refuses_home_and_root(snapshot):
    import pathlib
    with pytest.raises(SyncError):
        sync(snapshot, pathlib.Path.home())
    with pytest.raises(SyncError):
        sync(snapshot, "/")
    with pytest.raises(SyncError):
        sync(snapshot, "")


def test_refuses_a_path_that_is_a_file(snapshot, dest):
    dest.mkdir(parents=True, exist_ok=True)
    blocker = dest / "a-file"
    blocker.write_text("x")
    with pytest.raises(SyncError):
        sync(snapshot, blocker)


# ------------------------------------------------------------------ plumbing
def test_filters_narrow_what_is_synced(machine, dest):
    PromptHistory(tool="codex").sync(dest)
    assert all(f.startswith("codex/") for f in names(dest)
               if not f.startswith("."))


def test_config_defaults_drive_a_bare_sync(machine, dest):
    report = PromptHistory(sync_folder=str(dest), sync_layout="single").sync()
    assert report.written == ["prompts.md"]


def test_sync_state_round_trips(machine, dest):
    write_sync_state({"sync_folder": str(dest), "sync_enabled": True,
                      "ignored_key": "dropped"})
    state = read_sync_state()
    assert state["sync_folder"] == str(dest)
    assert state["sync_enabled"] is True
    assert "ignored_key" not in state


def test_saved_state_reaches_the_config(machine, dest):
    from prompthistory import Config
    write_sync_state({"sync_folder": str(dest), "sync_enabled": True})
    config = Config.load()
    assert config.sync_folder == str(dest)
    assert config.sync_enabled is True

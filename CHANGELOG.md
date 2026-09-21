# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

## [1.1.0] - 2026-09-21

### Added
- Folder sync. Mirror your prompts into a folder on disk, as
  `tool/project/prompts.md` by default. Run it with `prompt-history sync`,
  from the panel in the UI, or automatically on every scan.
- The structure is configurable: one file per project, per session, or one
  file for everything, with or without the tool folder, as Markdown, plain
  text, JSON or CSV.
- A native folder chooser in the UI, so you can pick a destination instead
  of typing a path.
- `PromptHistory.sync()` in the library, and a `[sync]` config section.

### Notes
- Sync only rewrites a file when its content changed, and generated files
  carry no timestamp of their own, so a watched folder stays quiet.
- Pruning removes only files recorded in the manifest that sync itself
  wrote. Anything else in the folder is left alone. A folder with no
  manifest is never pruned.

## [1.0.0] - 2026-09-21

First release.

### Added
- Readers for Claude Code transcripts, Claude Code recent-prompt history,
  Codex rollouts, the Codex composer history log, and the Codex sqlite thread
  index.
- De-duplication so a prompt recovered from a history log is not listed twice
  when a transcript already covers it.
- Browser UI on loopback with search, a tool and project and session tree,
  per-prompt copy, copy with context, and copy all.
- Organised zip export: prompts filed `by-session` and `by-date`, plus CSV,
  JSON, Markdown and plain text, with a manifest.
- Exports as Markdown, JSON, CSV, plain text or zip, from the CLI or the UI,
  honouring the active filters.
- Python API: `PromptHistory`, `Config`, `Prompt`, `Session`.
- Config file in TOML or JSON, environment variables, and CLI flags, layered
  in that order of priority.
- Commands: `serve`, `list`, `search`, `sessions`, `export`, `stats`,
  `sources`, `config`.

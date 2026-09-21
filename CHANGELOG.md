# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

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

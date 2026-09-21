# Changelog

All notable changes to this project are documented here.
This project follows [semantic versioning](https://semver.org/).

## [1.3.0] - 2026-09-22

### Added
- Visual README with reproducible screenshots of fictional prompt history,
  analytics, and share cards; no personal history is used in documentation images.
- Shareable activity cards with local PNG rendering, preview, light/dark themes,
  optional project names, clipboard copy, and native sharing where supported.
- Compact navigation and mobile project browsing, responsive analytics tables,
  stronger secondary-text contrast, and bounded long session titles.
- Entirely local analytics with project and tool comparisons, estimated activity
  and typing time, word/character/sentence counts, daily trends, weekday/hour
  patterns, streaks, prompt length distribution, and session rankings.
- Analytics date filters, adjustable typing speed and idle cutoff, project
  drill-down, and aggregate JSON downloads with documented assumptions.
- Coverage for overlapping tasks, idle gaps, filtering, missing/future dates,
  local midnight, Unicode, exports, and large histories; packaged analytics assets.

## [1.2.0] - 2026-09-21

### Packaging
- Verify installed CLI entry points and bundled browser assets independently of the source checkout.
- Validate configurable server ports and avoid overflowing the valid port range when retrying busy ports.
- Avoid unnecessary reverse DNS during local server startup, including on macOS.

### Fixed
- Exclude Codex approval-review/sub-agent transcripts, injected plugin and browser context,
  Claude task notifications, and tool traffic from user prompt history.
- Restore goal objectives from local attachments or surviving saved goal context;
  keep explicit recovery notes when original content is unavailable.
- Preserve repeated submissions and distinct projects while merging duplicate sources
  for the same session. Retain Codex thread titles and sidebar section metadata.
- Decode attachment headers, voice-input envelopes, and structured user answers.
- Render safe inline Markdown references, retain original text on copy, and bundle
  Lobe Codex/Claude icons for offline use.

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

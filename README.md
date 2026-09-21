# Prompt History

**Every prompt you have ever typed into Claude Code and Codex is already sitting on your disk. This reads it back.**

[![PyPI](https://img.shields.io/pypi/v/prompt-history.svg)](https://pypi.org/project/prompt-history/)
[![Python](https://img.shields.io/pypi/pyversions/prompt-history.svg)](https://pypi.org/project/prompt-history/)
[![License](https://img.shields.io/pypi/l/prompt-history.svg)](LICENSE)

```bash
pipx install prompt-history && prompt-history
```

Your browser opens. There is everything you have asked an AI to build, searchable, grouped, one click from your clipboard.

No dependencies. No account. No network. It never writes to your session files, it only reads them.

---

## Why you want this

You spent six months writing prompts. Some of them were really good. You have no idea where any of them are.

They are in `~/.claude/projects` and `~/.codex/sessions`, buried in JSONL transcripts next to a few hundred megabytes of tool output and model replies. Technically readable. Practically gone.

This pulls out your half of the conversation and nothing else.

- The prompt you wrote at 2am that finally fixed the build
- That one perfect refactor instruction you want to reuse
- Six months of how you actually talk to these tools, as a file you own

---

## Get it

```bash
pipx install prompt-history     # recommended
uv tool install prompt-history  # or this
pip install prompt-history      # or the classic
```

Python 3.9 or newer. That is the entire dependency list.

---

## Use it

### The browser UI

```bash
prompt-history
```

Search across everything, browse by tool then project then session, copy any prompt with one click, download the lot as a zip.

It binds to `127.0.0.1` and serves a single HTML file. Nothing is uploaded, because there is nothing to upload to.

### The terminal, if you live there

```bash
prompt-history list                         # everything, newest first
prompt-history search postgres migration    # both words, anywhere
prompt-history stats                        # what you have, at a glance
prompt-history sessions                     # every session, newest first
prompt-history sources                      # where it looked and what it found
```

Some real ones:

```bash
# Your long prompts from this quarter, as Markdown
prompt-history export -f md --since 2026-07-01 --min-words 60 -o good-prompts.md

# Everything you ever said to Codex about auth
prompt-history search auth --tool codex --full

# One project, one archive
prompt-history export -f zip --project checkout-api -o checkout-prompts.zip

# Pipe it anywhere
prompt-history list --json | jq -r '.[] | select(.words > 100) | .text'
```

`--tool`, `--since`, `--until`, `--min-words`, `--max-words`, `--project`, `--exclude-project`, `--search` and `--no-slash-commands` work on every command, including `serve`. Filter once, and the UI, the exports and the zip all agree.

### The zip, organised

```bash
prompt-history export -f zip
```

```
prompt-history-2026-09-21/
├── README.txt                  what is in here, and the counts
├── index.json                  the full machine readable snapshot
├── prompts.csv                 one row per prompt, opens in any spreadsheet
├── prompts.md                  everything, one document
├── prompts.txt                 everything, no markup
├── by-session/
│   └── claude/checkout-api/
│       └── 2026-09-14-fix-the-stripe-webhook.md
└── by-date/
    └── 2026-09-14.md
```

`by-session` is the folder you actually read. Each file is one conversation, your prompts in the order you typed them, with the project, model and branch at the top.

### As a library

```python
from prompthistory import PromptHistory

ph = PromptHistory(since="2026-01-01", tool="claude")

print(len(ph.prompts()))

for hit in ph.search("rate limit"):
    print(hit.timestamp, hit.project, hit.text[:80])

ph.write("q3.zip")                 # format inferred from the suffix
markdown = ph.render("md")         # or get it as a string
```

Every CLI flag is a keyword argument, and a typo raises instead of silently returning everything.

`Prompt` carries `text`, `timestamp`, `project`, `session_id`, `model`, `git_branch`, `turn`, `words` and `kind`. `Session` carries the prompts plus where they came from.

---

## Configure it

```bash
prompt-history config --init     # writes a commented template
prompt-history config --show     # prints the values actually in effect
```

Lives at `~/.config/prompt-history/config.toml`, or `config.json` if your Python is older than 3.11.

```toml
[sources]
claude_code = true
codex = true
include_subagents = false   # sub-agent prompts are written by the model, not you

[filter]
min_words = 5               # stop "yes", "continue" and "go on" from filling the UI
skip_slash_commands = true

[server]
port = 7777
open_browser = true
```

Config file, then `PROMPT_HISTORY_*` environment variables, then CLI flags. Last one wins.

---

## Where the prompts come from

| Source | Path | What it gives |
|---|---|---|
| Claude Code transcripts | `~/.claude/projects/<project>/<session>.jsonl` | Full text, timestamps, project, model, branch |
| Claude Code recent history | `~/.claude.json` | Prompts whose transcript is gone |
| Codex rollouts | `~/.codex/sessions/**/rollout-*.jsonl` | Full text and metadata |
| Codex composer history | `~/.codex/history.jsonl` | Everything typed at the composer |
| Codex thread index | `~/.codex/state*.sqlite` | Opening prompt of threads with no rollout file |

`CLAUDE_CONFIG_DIR` and `CODEX_HOME` are respected, lists included. Windows `%APPDATA%` too.

Three Codex rollout formats are supported, current and legacy, because the on-disk shape has changed a few times.

**Thrown away:** tool results, tool calls, assistant turns, compaction summaries, `[Request interrupted]` markers, sub-agent prompts, and injected context like `<system-reminder>`, `<environment_context>` and `<user_instructions>`. What is left is what you typed.

When a history log repeats a prompt a transcript already has, you see it once. `stats` tells you how many were folded together.

---

## What it cannot do

Claude Desktop and ChatGPT Desktop chats are not files on your disk. They are in encrypted app storage or only on a server.

Codex makes this visible: if you use the desktop app, the thread titles are cached locally but the prompt text is not. Prompt History counts those threads and tells you, rather than quietly pretending they are not there.

If somebody tells you they can export your Claude Desktop history from local files, check what they are actually reading.

---

## Privacy

Your prompts contain your API keys, your client names and your worst code. So:

- Session files are opened read only and never modified
- The server binds to loopback, and there is no outbound request anywhere in the codebase
- No telemetry, no analytics, no update check
- Zero third-party packages, so there is no supply chain to trust but this one

It is roughly 2,300 lines of stdlib Python plus one HTML file. Read it yourself, it will not take long.

---

## Contributing

Adding a source is one file.

```
prompthistory/
├── api.py             PromptHistory: scan, filter, snapshot
├── config.py          file, env and flags, layered
├── model.py           Prompt, Session, text cleaning, timestamps
├── export.py          md, json, csv, txt, zip
├── server.py          loopback HTTP
├── cli.py             the commands
├── sources/
│   ├── claude_code.py
│   └── codex.py       <- copy this one
└── web/index.html     the whole frontend
```

Write a module in `sources/` that returns `list[Session]`, call it from `PromptHistory.refresh()`, add a fixture to `tests/conftest.py`. Done.

```bash
git clone https://github.com/alshell7/prompt-history
cd prompt-history
pip install -e ".[dev]"
pytest
```

Cursor, Aider, Gemini CLI and Zed all keep local transcripts. Pull requests very welcome.

---

MIT. Icons from [Tabler](https://tabler.io/icons), MIT.

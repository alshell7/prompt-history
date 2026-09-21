"""Command line interface.

Every command reads the same config layering: config file, then
`PROMPT_HISTORY_*` environment variables, then the flags you pass here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import PromptHistory
from .config import Config, default_config_path, find_config, write_template
from .export import FORMATS, default_filename, render, write

FILTER_FLAGS = (
    "search", "tool", "since", "until", "min_words", "max_words",
    "projects", "exclude_projects", "skip_slash_commands",
)


def add_filter_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("filters")
    group.add_argument("-s", "--search", metavar="WORDS",
                       help="only prompts containing all of these words")
    group.add_argument("-t", "--tool", choices=["claude", "codex"],
                       help="limit to one tool")
    group.add_argument("--since", metavar="DATE", help="on or after, e.g. 2026-01-01")
    group.add_argument("--until", metavar="DATE", help="on or before")
    group.add_argument("--min-words", type=int, metavar="N",
                       help="skip prompts shorter than N words")
    group.add_argument("--max-words", type=int, metavar="N",
                       help="skip prompts longer than N words")
    group.add_argument("-p", "--project", action="append", dest="projects",
                       metavar="NAME", help="limit to a project (repeatable)")
    group.add_argument("--exclude-project", action="append", dest="exclude_projects",
                       metavar="NAME", help="drop a project (repeatable)")
    group.add_argument("--no-slash-commands", dest="skip_slash_commands",
                       action="store_true", default=None,
                       help="drop prompts that start with /")


def add_source_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("sources")
    group.add_argument("--no-claude", dest="claude_code", action="store_false",
                       default=None, help="skip Claude Code")
    group.add_argument("--no-codex", dest="codex", action="store_false",
                       default=None, help="skip Codex")
    group.add_argument("--include-subagents", action="store_true", default=None,
                       help="include prompts written by the model for sub-agents")
    group.add_argument("--no-recovered", dest="include_recovered",
                       action="store_false", default=None,
                       help="transcripts only, skip history logs and sqlite indexes")
    group.add_argument("--claude-dir", action="append", dest="claude_config_dirs",
                       metavar="PATH", help="extra Claude root to scan (repeatable)")
    group.add_argument("--codex-dir", action="append", dest="codex_homes",
                       metavar="PATH", help="extra Codex root to scan (repeatable)")
    group.add_argument("--config", metavar="PATH", help="use this config file")


def build_config(args: argparse.Namespace) -> Config:
    known = {
        key: value for key, value in vars(args).items()
        if value is not None and key not in
        ("command", "config", "func", "out", "format", "limit", "full",
         "json_out", "init", "show", "path", "force", "verbose", "quiet",
         "query")
    }
    try:
        return Config.load(getattr(args, "config", None), **known)
    except (TypeError, ValueError) as exc:
        raise SystemExit(str(exc))


def _dim(text: str) -> str:
    """Grey, but only when a human is watching. Honours NO_COLOR."""
    import os
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    return f"\033[2m{text}\033[0m"


def _ellipsis(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


# ------------------------------------------------------------------ commands
def cmd_serve(args) -> int:
    from .server import serve

    config = build_config(args)
    serve(config, verbose=args.verbose)
    return 0


def cmd_list(args) -> int:
    history = PromptHistory(build_config(args))
    records = history.prompt_records()
    if args.limit:
        records = records[: args.limit]

    if args.json_out:
        json.dump(records, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if not records:
        print("No prompts matched.", file=sys.stderr)
        return 0

    if args.full:
        for record in records:
            when = (record["timestamp"] or "no timestamp")[:19].replace("T", " ")
            print("\n" + _dim(f"{when}  {record['tool']}  {record['project_name']}  "
                               f"{_ellipsis(record['session_title'], 40)}"))
            print(record["text"])
        return 0

    width = max(len(r["tool"]) for r in records)
    for record in records:
        when = (record["timestamp"] or "                   ")[:16].replace("T", " ")
        print(f"{when}  {record['tool'].ljust(width)}  "
              f"{_ellipsis(record['project_name'], 18).ljust(18)}  "
              f"{_ellipsis(record['text'], 84)}")
    sys.stdout.flush()
    print(f"\n{len(records)} prompts.", file=sys.stderr)
    return 0


def cmd_sessions(args) -> int:
    history = PromptHistory(build_config(args))
    records = history.prompt_records()
    sessions = history.session_records(records)

    if args.json_out:
        json.dump(sessions, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if not sessions:
        print("No sessions matched.", file=sys.stderr)
        return 0

    for session in sessions:
        started = (session.get("started_at") or "")[:16].replace("T", " ") or "undated"
        print(f"{started}  {session['tool'].ljust(6)}  "
              f"{str(session['prompt_count']).rjust(4)} prompts  "
              f"{_ellipsis(session.get('project_name') or '', 18).ljust(18)}  "
              f"{_ellipsis(session.get('title') or session['id'], 52)}")
        if args.full:
            print(" " * 18 + _dim(f"{session['id']}  {session.get('origin_file') or ''}"))
    sys.stdout.flush()
    print(f"\n{len(sessions)} sessions.", file=sys.stderr)
    return 0


def cmd_export(args) -> int:
    config = build_config(args)
    if args.format:
        config.export_format = args.format
    fmt = config.export_format
    if fmt not in FORMATS:
        raise SystemExit(f"Unknown format {fmt!r}. Choose from: {', '.join(FORMATS)}")

    history = PromptHistory(config)
    snapshot = history.snapshot()
    target = args.out or config.output

    if not target:
        if fmt == "zip":
            target = default_filename("zip")     # binary must not go to a pipe
        else:
            payload = render(snapshot, fmt)
            sys.stdout.write(payload if isinstance(payload, str) else payload.decode())
            return 0

    written = write(snapshot, fmt, target)
    size = written.stat().st_size
    print(f"Wrote {snapshot['stats']['prompts']} prompts to {written} "
          f"({size / 1024:.1f} KB)", file=sys.stderr)
    return 0


def cmd_stats(args) -> int:
    history = PromptHistory(build_config(args))
    records = history.prompt_records()
    stats = history.stats(records)

    if args.json_out:
        json.dump(stats, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    print(f"Prompts   {stats['prompts']}")
    print(f"Sessions  {stats['sessions']}")
    print(f"Projects  {stats['projects']}")
    print(f"Words     {stats['words']:,}")
    early = (stats["earliest"] or "")[:16].replace("T", " ") or "n/a"
    late = (stats["latest"] or "")[:16].replace("T", " ") or "n/a"
    print(f"Range     {early} to {late}")

    if stats["by_source"]:
        print("\nBy source")
        width = max(len(k) for k in stats["by_source"])
        for label, count in sorted(stats["by_source"].items(), key=lambda kv: -kv[1]):
            print(f"  {label.ljust(width)}  {count}")
    if stats["by_project"]:
        print("\nBy project")
        top = list(stats["by_project"].items())[:12]
        width = max(len(k) for k, _ in top)
        for name, count in top:
            print(f"  {name.ljust(width)}  {count}")
    if stats["duplicates_removed"]:
        print(f"\nDropped {stats['duplicates_removed']} entries already covered "
              "by a transcript.")
    if stats["cloud_threads"]:
        print(f"\n{stats['cloud_threads']} Codex threads synced from ChatGPT hold "
              "titles only.\nTheir prompt text is not stored on this machine.")
    return 0


def cmd_sources(args) -> int:
    history = PromptHistory(build_config(args))
    stats = history.stats()
    print(f"Config    {stats['config_source']}")
    print("\nScanned")
    for root in stats["scanned_roots"] or ["  nothing"]:
        print(f"  {root}")
    print("\nFound")
    if stats["by_source"]:
        width = max(len(k) for k in stats["by_source"])
        for label, count in sorted(stats["by_source"].items(), key=lambda kv: -kv[1]):
            print(f"  {label.ljust(width)}  {count} prompts")
    else:
        print("  no prompts")
    cloud = history.cloud_threads()
    if cloud:
        print(f"\n  {len(cloud)} ChatGPT-synced Codex threads (titles only, "
              "no prompt text on disk)")
    return 0


def cmd_config(args) -> int:
    if args.init:
        try:
            written = write_template(args.path, force=args.force)
        except FileExistsError as exc:
            raise SystemExit(f"{exc} already exists. Pass --force to overwrite.")
        print(f"Wrote {written}")
        return 0
    if args.show:
        json.dump(Config.load(args.path).to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    found = find_config()
    print(found if found else f"No config file. `prompt-history config --init` "
                              f"would create {default_config_path()}")
    return 0


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prompt-history",
        description="Read back the prompts you typed into Claude Code and Codex.",
        epilog="Run without a command to open the browser UI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve_cmd = sub.add_parser("serve", help="open the browser UI")
    serve_cmd.add_argument("--port", type=int, help="default 7777")
    serve_cmd.add_argument("--host", help="default 127.0.0.1")
    serve_cmd.add_argument("--no-browser", dest="open_browser", action="store_false",
                           default=None, help="start the server but do not open a tab")
    serve_cmd.add_argument("--verbose", action="store_true", help="log requests")
    serve_cmd.set_defaults(func=cmd_serve)

    list_cmd = sub.add_parser("list", help="print prompts to the terminal")
    list_cmd.add_argument("-n", "--limit", type=int, help="show at most N")
    list_cmd.add_argument("--full", action="store_true", help="print whole prompts")
    list_cmd.add_argument("--json", dest="json_out", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    search_cmd = sub.add_parser("search", help="shorthand for `list --search`")
    search_cmd.add_argument("query", nargs="+")
    search_cmd.add_argument("-n", "--limit", type=int)
    search_cmd.add_argument("--full", action="store_true")
    search_cmd.add_argument("--json", dest="json_out", action="store_true")
    search_cmd.set_defaults(func=cmd_list)

    sessions_cmd = sub.add_parser("sessions", help="list sessions, newest first")
    sessions_cmd.add_argument("--full", action="store_true",
                              help="also show ids and source files")
    sessions_cmd.add_argument("--json", dest="json_out", action="store_true")
    sessions_cmd.set_defaults(func=cmd_sessions)

    export_cmd = sub.add_parser("export", help="write prompts to a file")
    export_cmd.add_argument("-f", "--format", choices=list(FORMATS),
                            help="default md; zip is the organised archive")
    export_cmd.add_argument("-o", "--out", metavar="PATH",
                            help="file or directory; omit to print to stdout")
    export_cmd.set_defaults(func=cmd_export)

    stats_cmd = sub.add_parser("stats", help="summarise what was found")
    stats_cmd.add_argument("--json", dest="json_out", action="store_true")
    stats_cmd.set_defaults(func=cmd_stats)

    sources_cmd = sub.add_parser("sources", help="show where it looked")
    sources_cmd.set_defaults(func=cmd_sources)

    config_cmd = sub.add_parser("config", help="show or create the config file")
    config_cmd.add_argument("--init", action="store_true", help="write a template")
    config_cmd.add_argument("--show", action="store_true", help="print effective values")
    config_cmd.add_argument("--path", metavar="PATH", help="target a specific file")
    config_cmd.add_argument("--force", action="store_true", help="overwrite on --init")
    config_cmd.set_defaults(func=cmd_config)

    for cmd in (serve_cmd, list_cmd, search_cmd, sessions_cmd, export_cmd,
                stats_cmd, sources_cmd):
        add_filter_flags(cmd)
        add_source_flags(cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)

    # Bare `prompt-history` opens the UI; `prompt-history --port 8080` too.
    commands = set(parser._subparsers._group_actions[0].choices)  # type: ignore[attr-defined]
    if not argv or (argv[0] not in commands and not argv[0].startswith("-")):
        pass
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv = ["serve"] + argv

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["serve"])

    if getattr(args, "query", None):
        args.search = " ".join(args.query)

    if not hasattr(args, "verbose"):
        args.verbose = False

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:               # `prompt-history list | head`
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

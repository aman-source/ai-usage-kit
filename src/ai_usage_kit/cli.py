"""Command line entry point for ai-usage-kit (also installed as `eai`)."""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_usage_kit.analyze.export import build_export, write_export
from ai_usage_kit.analyze.report import render_markdown, render_terminal
from ai_usage_kit.analyze.rules import Thresholds
from ai_usage_kit.analyze.runner import analyze
from ai_usage_kit.defaults import commands
from ai_usage_kit.defaults.items import ITEM_IDS
from ai_usage_kit.defaults.managed import system_managed
from ai_usage_kit.defaults.settings import SettingsError
from ai_usage_kit.trim.hook import now_seconds, run_hook, state_dir
from ai_usage_kit.trim.install import is_persistent_install


def parse_since(value: str) -> datetime:
    days = re.fullmatch(r"(\d+)d", value)
    if days:
        return datetime.now(UTC) - timedelta(days=int(days.group(1)))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected Nd (e.g. 30d) or a date (YYYY-MM-DD), got {value!r}"
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number of 1 or more, got {value!r}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eai", description="Cut AI coding agent token spend.")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_analyze(sub.add_parser("analyze", help="report where tokens went in local Claude Code sessions"))
    init = sub.add_parser("init", help="apply cost-saving Claude Code settings (see --dry-run)")
    init.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    sub.add_parser("status", help="show each item: on, off, skipped or set by your organization")
    for name in ("on", "off"):
        toggle = sub.add_parser(name, help=f"turn one item {name}")
        toggle.add_argument("item", choices=ITEM_IDS)
        toggle.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
    remove = sub.add_parser("uninstall", help="remove every change and restore your settings")
    remove.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    hook = sub.add_parser("hook", help="internal: run a Claude Code hook (reads JSON on stdin)")
    hook.add_argument("name", choices=("trim", "read-once"))
    return parser


def _add_analyze(p: argparse.ArgumentParser) -> None:
    d = Thresholds()
    p.add_argument("--root", type=Path, help="session log folder (default: ~/.claude/projects)")
    p.add_argument("--project", help="only projects whose folder name contains this text")
    p.add_argument("--since", type=parse_since, help="only sessions modified since: 30d or YYYY-MM-DD")
    p.add_argument("--top", type=positive_int, default=10, help="sessions to list (default: 10)")
    p.add_argument("--out", type=Path, help="write the full Markdown report here")
    p.add_argument("--export", type=Path, help="write a numbers-only JSON summary here")
    p.add_argument("--large-output-chars", type=int, default=d.large_output_chars)
    p.add_argument("--cache-gap-minutes", type=float, default=d.cache_gap.total_seconds() / 60)
    p.add_argument("--cache-read-ratio", type=float, default=d.cache_read_ratio)
    p.add_argument("--instruction-lines", type=int, default=d.instruction_lines)
    p.add_argument("--mcp-servers", type=int, default=d.mcp_servers)
    p.add_argument("--long-session-calls", type=int, default=d.long_session_calls)
    p.add_argument("--long-session-context", type=int, default=d.long_session_context)


def _run_analyze(args: argparse.Namespace) -> int:
    home = Path.home()
    root = args.root or home / ".claude" / "projects"
    thresholds = Thresholds(
        large_output_chars=args.large_output_chars,
        cache_gap=timedelta(minutes=args.cache_gap_minutes),
        cache_read_ratio=args.cache_read_ratio,
        instruction_lines=args.instruction_lines,
        mcp_servers=args.mcp_servers,
        long_session_calls=args.long_session_calls,
        long_session_context=args.long_session_context,
    )
    result = analyze(root, home, project=args.project, since=args.since, thresholds=thresholds)
    for path in result.stats.unreadable:
        print(f"warning: could not read {path}", file=sys.stderr)
    if not result.sessions:
        print(f"No Claude Code sessions found under {root}.")
        return 0
    print(render_terminal(result, args.top))
    try:
        if args.out:
            args.out.write_text(render_markdown(result, args.top), encoding="utf-8")
            print(f"Report written to {args.out}")
        if args.export:
            from ai_usage_kit import __version__

            write_export(args.export, build_export(result, __version__))
            print(f"Numbers-only export written to {args.export}")
    except OSError as error:
        print(f"error: cannot write {error.filename}: {error.strerror or error}", file=sys.stderr)
        return 1
    return 0


def _tolerant_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _defaults_command(args: argparse.Namespace) -> int:
    found = shutil.which("eai")
    eai_path = found if found and is_persistent_install(found) else None
    ctx = commands.Context(Path.home(), os.environ, system_managed(), Path.cwd(), eai_path)
    try:
        if args.command == "init":
            result = commands.init(ctx, args.dry_run)
        elif args.command == "status":
            result = commands.status(ctx)
        elif args.command == "on":
            result = commands.turn_on(ctx, args.item, args.dry_run)
        elif args.command == "off":
            result = commands.turn_off(ctx, args.item, args.dry_run)
        else:
            result = commands.uninstall(ctx, args.dry_run)
    except SettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        where = f" ({error.filename})" if error.filename else ""
        print(f"error: {error.strerror or error}{where}; nothing else was changed", file=sys.stderr)
        return 1
    print("\n".join(result.lines), file=sys.stderr if result.code else sys.stdout)
    return result.code


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["hook"] and args_list[1:2] in (["trim"], ["read-once"]):
        return _run_hook(args_list[1])  # the hook prints only its reply and never changes the console
    _tolerant_console()
    args = build_parser().parse_args(argv)
    if args.command == "analyze":
        return _run_analyze(args)
    return _defaults_command(args)


def _run_hook(name: str) -> int:
    raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
    state = state_dir(os.environ, Path.home())
    if name == "read-once":
        from ai_usage_kit.read_once.hook import run_hook as run_read_once

        out = run_read_once(raw, state, now_seconds())
    else:
        out = run_hook(raw, state, now_seconds())
    if out:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(out)
        else:
            sys.stdout.write(out.decode("utf-8"))
        sys.stdout.flush()
    return 0

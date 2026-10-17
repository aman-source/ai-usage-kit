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
from typing import TYPE_CHECKING

# Heavier modules (analyze, the settings commands, navigate, mcp) are imported where they are used, so a
# quick command such as `eai navigate where X`, which Claude runs often, starts fast.
from ai_usage_kit.defaults.items import ITEM_IDS, RECOMMENDED
from ai_usage_kit.defaults.managed import managed_dir, system_managed
from ai_usage_kit.defaults.settings import SettingsError
from ai_usage_kit.trim.hook import now_seconds, run_hook, state_dir
from ai_usage_kit.trim.install import is_persistent_install

if TYPE_CHECKING:
    from ai_usage_kit.defaults.commands import Context


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
        toggle = sub.add_parser(name, help=f"turn items {name} (several at once, or: recommended)")
        toggle.add_argument(
            "items",
            metavar="item",
            nargs="+",
            choices=(*ITEM_IDS, "recommended"),
            help=f"one or more of: {', '.join(ITEM_IDS)}; or recommended ({', '.join(RECOMMENDED)})",
        )
        toggle.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
    mcp = sub.add_parser("mcp", help="read-only check: MCP tool search state, servers and their use")
    mcp.add_argument("--since", type=parse_since, default="30d", help="usage window: 30d or YYYY-MM-DD")
    mcp.add_argument("--project", type=Path, help="project folder (default: current directory)")
    navigate = sub.add_parser("navigate", help="local Python code graph: where, who-uses, impact, outline")
    _add_navigate(navigate)
    remove = sub.add_parser("uninstall", help="remove every change and restore your settings")
    remove.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    hook = sub.add_parser("hook", help="internal: run a Claude Code hook (reads JSON on stdin)")
    hook.add_argument("name", choices=("trim", "read-once"))
    return parser


def _add_analyze(p: argparse.ArgumentParser) -> None:
    from ai_usage_kit.analyze.rules import Thresholds

    d = Thresholds()
    p.add_argument(
        "--root", type=Path, help="log folder (default: ~/.claude/projects or $CLAUDE_CONFIG_DIR/projects)"
    )
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


def _projects_root(home: Path) -> Path:
    """Claude Code's session logs: <config dir>/projects, where the config dir honors CLAUDE_CONFIG_DIR."""
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(custom).expanduser() if custom else home / ".claude") / "projects"


def _run_analyze(args: argparse.Namespace) -> int:
    from ai_usage_kit.analyze.export import build_export, write_export
    from ai_usage_kit.analyze.report import render_markdown, render_terminal
    from ai_usage_kit.analyze.rules import Thresholds
    from ai_usage_kit.analyze.runner import analyze

    home = Path.home()
    root = args.root or _projects_root(home)
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


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _add_navigate(p: argparse.ArgumentParser) -> None:
    from ai_usage_kit.navigate.tools import KINDS

    nav = p.add_subparsers(dest="nav_command", required=True)
    where = nav.add_parser("where", help="where a symbol is defined (path:start-end); NAME may be a|b or *x*")
    where.add_argument("symbol", metavar="NAME")
    uses = nav.add_parser("who-uses", help="who calls, imports or subclasses a symbol (path:line)")
    uses.add_argument("symbol", metavar="NAME")
    uses.add_argument("--kind", dest="kinds", action="append", choices=KINDS, help="only these kinds")
    uses.add_argument("--page", type=_positive_int, default=1)
    uses.add_argument("--depth", type=int, choices=(1, 2, 3), default=1, help="2 or 3: callers of callers")
    impact = nav.add_parser("impact", help="what a change affects: a symbol, or a unified diff with --diff")
    impact.add_argument("symbol", metavar="NAME", nargs="?")
    impact.add_argument("--diff", metavar="FILE", help="unified diff file, or - for standard input")
    impact.add_argument("--page", type=_positive_int, default=1)
    impact.set_defaults(nav_parser=impact)
    outline = nav.add_parser("outline", help="definitions in one Python file with line spans")
    outline.add_argument("path", metavar="PATH")
    hooks = nav.add_parser("hooks", help="git hooks that refresh the graph after git pull (this repo)")
    hooks.add_argument("action", choices=("on", "off", "status"))
    nav.add_parser("status", help="graph and hooks of this repo")
    nav.add_parser("clean", help="delete this repo's graph (it is rebuilt on the next question)")
    update = nav.add_parser("update", help="build or refresh the graph of this repo")
    update.add_argument("--repo", type=Path)
    update.add_argument("--background", action="store_true")


def _run_navigate(args: argparse.Namespace) -> int:
    from ai_usage_kit.navigate import query

    tool = args.nav_command
    if tool in ("hooks", "status", "clean"):
        return _navigate_admin(args)
    values: dict[str, object] = {}
    if tool == "outline":
        values["path"] = args.path
    else:
        values["symbol"] = args.symbol
    if tool == "who-uses":
        values.update(kinds=args.kinds, page=args.page, depth=args.depth)
    if tool == "impact":
        values["page"] = args.page
        if bool(args.symbol) == bool(args.diff):
            args.nav_parser.error("give a symbol or --diff (exactly one)")
        if args.diff:
            values["diff"] = _read_diff(args.diff, args.nav_parser)
    _emit(query.answer(tool, values, Path.cwd(), os.environ, Path.home()))
    return 0


def _navigate_admin(args: argparse.Namespace) -> int:
    try:
        return _navigate_admin_steps(args)
    except OSError as error:
        where = f" ({error.filename})" if error.filename else ""
        print(f"error: {error.strerror or error}{where}", file=sys.stderr)
        return 1


def _navigate_admin_steps(args: argparse.Namespace) -> int:
    from ai_usage_kit.navigate import files, githooks, install, store

    cwd, home = Path.cwd(), Path.home()
    root = files.repo_root(cwd)
    gdir = files.git_dir(root) if root else None
    if root is None or gdir is None:
        print("error: not a git repository (or git is not installed)", file=sys.stderr)
        return 1
    if args.nav_command == "status":
        print(install.graph_status(cwd, os.environ, home, now_seconds()) or "")
        print("\n".join(githooks.status(root)))
        return 0
    if args.nav_command == "clean":
        folder = store.graphs_root(os.environ, home) / store.repo_id(gdir)
        store.remove_graph(folder)
        print(f"deleted the graph of {root}")
        return 0
    if args.action == "status":
        print("\n".join(githooks.status(root)))
        return 0
    if args.action == "off":
        lines = githooks.remove(root)
        if not any("not removed" in line for line in lines):
            install.forget_repo(os.environ, home, root)
        print("\n".join(lines))
        return 0
    eai = _eai_path()
    if eai is None:
        from ai_usage_kit.defaults.commands import INSTALL_TIP

        print(INSTALL_TIP, file=sys.stderr)
        return 1
    lines = githooks.install(root, eai)
    failed = lines[0].startswith(("error", "refused"))
    if any(line.endswith((": created", ": added", ": already there")) for line in lines):
        install.remember_repo(os.environ, home, root)  # so eai off navigate / uninstall remove them
    print("\n".join(lines), file=sys.stderr if failed else sys.stdout)
    return 1 if failed else 0


def _read_diff(source: str, parser: argparse.ArgumentParser) -> str:
    if source == "-":
        stream = getattr(sys.stdin, "buffer", None)
        data = stream.read() if stream is not None else sys.stdin.read().encode("utf-8")
        return bytes(data).decode("utf-8", "replace")
    try:
        return Path(source).read_bytes().decode("utf-8", "replace")
    except OSError as error:
        parser.error(f"cannot read {source}: {error.strerror or error}")
    raise AssertionError("unreachable")


def _emit(text: str) -> None:
    """UTF-8 bytes whatever the console code page, so paths and snippets never fail to print."""
    sys.stdout.flush()
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(text)
        return
    buffer.write((text + "\n").encode("utf-8"))
    buffer.flush()


def managed_mcp_path() -> Path:
    return managed_dir() / "managed-mcp.json"


def _run_mcp(args: argparse.Namespace) -> int:
    from ai_usage_kit.analyze.runner import analyze
    from ai_usage_kit.mcp_check.config import Sources, read_servers, settings_env_blocks
    from ai_usage_kit.mcp_check.report import render, usage_by_server
    from ai_usage_kit.mcp_check.toolsearch import effective_env, tool_search

    home = Path.home()
    project = (args.project or Path.cwd()).resolve()
    src = Sources(home, os.environ, project, system_managed(), managed_mcp_path())
    servers, notes = read_servers(src)
    search = tool_search(effective_env(os.environ, settings_env_blocks(src)))
    counts: dict[str, int] = {}
    counts_here: dict[str, int] = {}
    folder = re.sub(r"[^A-Za-z0-9]", "-", str(project)).lower()  # Claude Code's log folder for this project
    try:
        for session in analyze(_projects_root(home), home, since=args.since).sessions:
            for group, n in session.tool_counts().items():
                counts[group] = counts.get(group, 0) + n
                if session.project.lower() == folder:
                    counts_here[group] = counts_here.get(group, 0) + n
    except OSError as error:
        notes.append(f"could not read session logs: {error.strerror or error}")
    days = max(1, round((datetime.now(UTC) - args.since).total_seconds() / 86400))
    here, everywhere = usage_by_server(counts_here), usage_by_server(counts)
    print("\n".join(render(search, servers, here, everywhere, notes, days)))
    return 0


def _tolerant_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _eai_path() -> str | None:
    """The permanent `eai` on PATH (hooks and Claude run it by name), or None."""
    found = shutil.which("eai")
    return found if found and is_persistent_install(found) else None


def _defaults_command(args: argparse.Namespace) -> int:
    from ai_usage_kit.defaults import commands

    eai_path = _eai_path()
    version = None
    items = _expand_items(args.items) if args.command in ("on", "off") else []
    if args.command == "on" and "subagent-model" in items:
        from ai_usage_kit.analyze.versions import newest_claude_code_version

        since = datetime.now(UTC) - timedelta(days=30)
        version = newest_claude_code_version(_projects_root(Path.home()), since)
    ctx = commands.Context(Path.home(), os.environ, system_managed(), Path.cwd(), eai_path, version)
    try:
        if args.command == "init":
            result = commands.init(ctx, args.dry_run)
        elif args.command == "status":
            result = commands.status(ctx)
        elif args.command in ("on", "off"):
            return _toggle_items(ctx, args, items)
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


def _expand_items(names: Sequence[str]) -> list[str]:
    """Item ids in the order given, `recommended` expanded, each once."""
    expanded = [i for name in names for i in (RECOMMENDED if name == "recommended" else (name,))]
    return list(dict.fromkeys(expanded))


def _toggle_items(ctx: Context, args: argparse.Namespace, items: list[str]) -> int:
    """Each item on its own, so one refused item does not stop the rest; prints as it goes."""
    from ai_usage_kit.defaults import commands
    from ai_usage_kit.defaults.items import NOT_RECOMMENDED

    toggle = commands.turn_on if args.command == "on" else commands.turn_off
    code = 0
    if args.dry_run:
        print(commands.DRY_RUN)
    for item in items:
        result = toggle(ctx, item, args.dry_run)
        lines = [line for line in result.lines if line != commands.DRY_RUN]
        if result.code and len(items) > 1:
            lines = [f"{item}: {line}" for line in lines]  # say which item an error belongs to
        print("\n".join(lines), file=sys.stderr if result.code else sys.stdout)
        code = max(code, result.code)
    left_off = [item for item in NOT_RECOMMENDED if item not in args.items]
    if args.command == "on" and "recommended" in args.items and left_off:
        print(f"left off (turn on one by one if they fit): {', '.join(left_off)}")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:2] == ["navigate", "update"]:
        return _navigate_update(args_list[2:])  # run by git hooks and background builds: always exit 0
    if args_list[:1] == ["hook"] and args_list[1:2] in (["trim"], ["read-once"]):
        return _run_hook(args_list[1])  # the hook prints only its reply and never changes the console
    _tolerant_console()
    if args_list[:1] == ["navigate"]:  # only the navigate parser: no analyze or settings modules to load
        parser = argparse.ArgumentParser(prog="eai")
        sub = parser.add_subparsers(dest="command", required=True)
        _add_navigate(sub.add_parser("navigate", help="local Python code graph"))
        return _run_navigate(parser.parse_args(args_list))
    args = build_parser().parse_args(argv)
    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "mcp":
        return _run_mcp(args)
    if args.command == "navigate":
        return _run_navigate(args)
    return _defaults_command(args)


UPDATE_TEXT = {
    "built": "graph built",
    "fresh": "graph is up to date",
    "locked": "another build is running",
    "no-git": "not a git repository (or git is not installed); navigate works in git repos",
    "failed": "build failed; the previous graph is kept (see eai navigate status)",
}


def _navigate_update(argv: list[str]) -> int:
    from ai_usage_kit.navigate import build

    parser = argparse.ArgumentParser(prog="eai navigate update")
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--background", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0
    repo = args.repo or Path.cwd()
    if args.background:
        started = build.start_background(repo)
        print("background build started" if started else "could not start a background build")
        return 0
    print(UPDATE_TEXT[build.update(repo, os.environ, Path.home())])
    return 0


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

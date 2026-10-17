"""Answer one navigate question for the repository that contains `cwd`. Never raises.

The answer comes from the published graph. A stale graph still answers, with a line naming the changed
files, and a background rebuild starts (at most once per 10 seconds). Without a graph, a background build
starts and the answer waits for it up to `wait_seconds`.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_usage_kit.navigate import build, files, freshness, snapshot, store
from ai_usage_kit.navigate.graph.config import Config, RepoConfig
from ai_usage_kit.navigate.tools import ToolError, Tools

NOT_GIT = "not a git repository (or git is not installed); navigate works in git repos"
BUILDING = "graph is being built (first time for this repo); use Grep/Read for now"
REQUEST_GAP = 10.0
POLL = 0.25
TOOLS = ("where", "who-uses", "impact", "outline")


def answer(
    tool: str,
    args: Mapping[str, Any],
    cwd: Path,
    environ: Mapping[str, str],
    home: Path,
    wait_seconds: float = 15.0,
) -> str:
    try:
        info = files.repo_info(cwd)
        if info is None:
            return NOT_GIT
        root, gdir = info
        state = store.graph_dir(environ, home, gdir, root)
        graph = _open(state)
        if graph is None:
            _request(state, root, force=True)
            graph = _wait(state, wait_seconds)
            if graph is None:
                return BUILDING
        try:
            cfg = Config(repos=[RepoConfig(root.name, root)])
            try:
                idx = graph.index(cfg)
            except ValueError:
                _request(state, root, force=True)
                return BUILDING
            fresh = freshness.check(root, graph.meta)
            if fresh.stale:
                _request(state, root, force=False)
            tools = Tools(idx, cfg, freshness=lambda: fresh, show_repo=False)
            if tool == "outline":
                args = {**args, "path": _from_cwd(str(args["path"]), cwd)}
            return _dispatch(tools, tool, args)
        finally:
            graph.close()
    except ToolError as error:
        return f"error: {error}"
    except Exception as error:  # fail open: Claude falls back to Grep/Read
        return f"navigate failed ({type(error).__name__}); use Grep/Read"


def _dispatch(tools: Tools, tool: str, args: Mapping[str, Any]) -> str:
    if tool == "where":
        return tools.where(args["symbol"])
    if tool == "who-uses":
        return tools.who_uses(
            args["symbol"], kinds=args.get("kinds"), page=args.get("page", 1), depth=args.get("depth", 1)
        )
    if tool == "impact":
        return tools.impact(symbol=args.get("symbol"), diff=args.get("diff"), page=args.get("page", 1))
    if tool == "outline":
        return tools.outline(args["path"])
    raise ToolError(f"unknown command {tool}; use: {', '.join(TOOLS)}")


def _from_cwd(path: str, cwd: Path) -> str:
    """A relative path that names an existing file from the current folder becomes absolute."""
    candidate = Path(path)
    if candidate.is_absolute():
        return path
    try:
        here = (cwd / candidate).resolve()
    except OSError:
        return path
    return str(here) if here.is_file() else path


def _open(state: Path) -> snapshot.Graph | None:
    current = build.current_db(state)
    return snapshot.open_graph(current) if current is not None else None


def _wait(state: Path, seconds: float) -> snapshot.Graph | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(POLL)
        graph = _open(state)
        if graph is not None:
            return graph
    return None


def _request(state: Path, root: Path, force: bool) -> None:
    """Start a background build unless one was requested in the last few seconds (or force)."""
    marker = state / "requested"
    try:
        recent = time.time() - marker.stat().st_mtime < REQUEST_GAP
    except OSError:
        recent = False
    if recent and not force:
        return
    store.write_private_text(marker, str(time.time()))
    build.start_background(root)

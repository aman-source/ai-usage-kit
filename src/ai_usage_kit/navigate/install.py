"""What `eai on/off navigate` adds besides the rule file: allow rules, recorded hook repos, graph status."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from pathlib import Path

from ai_usage_kit.defaults.items import NAVIGATE_ALLOW_RULES
from ai_usage_kit.hook_files import write_private
from ai_usage_kit.navigate import build, files, freshness, githooks, snapshot, store
from ai_usage_kit.trim.hook import state_dir

ALLOW_RULES = NAVIGATE_ALLOW_RULES


def _repos_file(environ: Mapping[str, str], home: Path) -> Path:
    return state_dir(environ, home) / "navigate" / "hook-repos.json"


def recorded_repos(environ: Mapping[str, str], home: Path) -> list[Path]:
    try:
        data = json.loads(_repos_file(environ, home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Path(p) for p in data if isinstance(p, str)] if isinstance(data, list) else []


def remember_repo(environ: Mapping[str, str], home: Path, root: Path) -> None:
    known = [str(p) for p in recorded_repos(environ, home)]
    entry = str(root.resolve())
    if entry not in known:
        write_private(_repos_file(environ, home), json.dumps([*known, entry], indent=1))


def forget_repo(environ: Mapping[str, str], home: Path, root: Path) -> None:
    entry = str(root.resolve())
    kept = [str(p) for p in recorded_repos(environ, home) if str(p) != entry]
    if kept:
        write_private(_repos_file(environ, home), json.dumps(kept, indent=1))
    else:
        _repos_file(environ, home).unlink(missing_ok=True)


def remove_everything(environ: Mapping[str, str], home: Path) -> list[str]:
    """Remove eai's hooks from every recorded repo that still exists, and delete all graphs."""
    removed = 0
    for root in recorded_repos(environ, home):
        if root.exists() and not githooks.remove(root)[0].startswith("error"):
            removed += 1
    _repos_file(environ, home).unlink(missing_ok=True)
    store.remove_all(environ, home)
    with contextlib.suppress(OSError):
        _repos_file(environ, home).parent.rmdir()
    return [f"  navigate    hooks removed from {removed} repo{'s' if removed != 1 else ''}; graphs deleted"]


def graph_status(cwd: Path, environ: Mapping[str, str], home: Path, now: float) -> str | None:
    """One line about the current repo's graph and hooks, or None outside a git repo. Creates nothing."""
    info = files.repo_info(cwd)
    if info is None:
        return None
    root, gdir = info
    on = sum(line.endswith(": on") for line in githooks.status(root))
    hooks = "on" if on == len(githooks.HOOKS) else "off" if on == 0 else "partly on"
    current = build.current_db(store.graphs_root(environ, home) / store.repo_id(gdir))
    meta = snapshot.read_meta(current) if current is not None else None
    if meta is None:
        return f"graph: not built yet (first query builds it); hooks {hooks}"
    fresh = freshness.check(root, meta)
    state = f"stale ({len(fresh.changed)} files changed)" if fresh.stale else "up to date"
    built_at = meta.get("built_at")
    age = _age(now - built_at) if isinstance(built_at, int | float) else "at an unknown time"
    files_count = meta.get("indexed", "?")
    return f"graph: {fresh.indexed or 'no commit'}, {files_count} files, built {age}, {state}; hooks {hooks}"


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"

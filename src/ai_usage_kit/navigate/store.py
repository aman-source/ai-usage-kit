"""Where graphs live: <eai state>/navigate/graphs/<repo-id>/, outside every repository.

<repo-id> is derived from the working tree's git dir, so each clone and each worktree has its own graph.
`root.txt` records the working tree path, for display and to delete graphs of repos that no longer exist.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ai_usage_kit.hook_files import private_dir
from ai_usage_kit.trim.hook import state_dir

MAX_IDLE_DAYS = 90


def graphs_root(environ: Mapping[str, str], home: Path) -> Path:
    return state_dir(environ, home) / "navigate" / "graphs"


def repo_id(git_dir: Path) -> str:
    key = os.path.normcase(str(git_dir.resolve()))
    return hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def graph_dir(environ: Mapping[str, str], home: Path, git_dir: Path, root: Path) -> Path:
    base = graphs_root(environ, home)
    for folder in (base.parent, base):
        private_dir(folder)
    folder = base / repo_id(git_dir)
    private_dir(folder)
    marker = folder / "root.txt"
    try:
        known = marker.read_text(encoding="utf-8")
    except OSError:
        known = None
    if known != str(root):
        write_private_text(marker, str(root))
    return folder


def write_private_text(path: Path, text: str) -> None:
    """Write a small file readable only by its owner; never creates folders; errors ignored."""
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, 0o600)


def prune(environ: Mapping[str, str], home: Path, now: float) -> int:
    """Delete graphs of repos that are gone or unused for 90 days. Never raises."""
    removed = 0
    try:
        folders = [p for p in graphs_root(environ, home).iterdir() if p.is_dir()]
    except OSError:
        return 0
    for folder in folders:
        try:
            root = (folder / "root.txt").read_text(encoding="utf-8").strip()
            newest = max((p.stat().st_mtime for p in folder.iterdir()), default=0.0)
        except OSError:
            continue
        if not Path(root).exists() or now - newest > MAX_IDLE_DAYS * 86400:
            _rmtree(folder)
            removed += 0 if folder.exists() else 1
    return removed


def remove_graph(folder: Path) -> None:
    _rmtree(folder)


def remove_all(environ: Mapping[str, str], home: Path) -> None:
    _rmtree(graphs_root(environ, home))
    with contextlib.suppress(OSError):
        graphs_root(environ, home).parent.rmdir()  # only when nothing else of navigate is left


def _rmtree(path: Path) -> None:
    """Delete a folder; read-only files are made writable first; files still in use are left for later."""

    def retry_writable(func: Callable[..., Any], target: str, _exc: object) -> None:
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry_writable)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=retry_writable)

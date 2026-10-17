"""Python files of a git working tree: tracked plus untracked-not-ignored, minus secrets and huge files."""

from __future__ import annotations

import functools
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath

from ai_usage_kit.navigate.graph.config import denied

MAX_BYTES = 2_000_000
GIT_TIMEOUT = 60


def _git(root: Path, *args: str) -> bytes | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},  # never rewrite the index while the user works
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _text(out: bytes | None) -> str | None:
    if out is None:
        return None
    return out.decode("utf-8", "replace").strip() or None


def repo_root(cwd: Path) -> Path | None:
    found = _text(_git(cwd, "rev-parse", "--show-toplevel"))
    return Path(found) if found else None


def git_dir(root: Path) -> Path | None:
    found = _text(_git(root, "rev-parse", "--absolute-git-dir"))
    return Path(found) if found else None


def repo_info(cwd: Path) -> tuple[Path, Path] | None:
    """(working tree root, its git folder) in one git call, or None outside a repository."""
    out = _git(cwd, "rev-parse", "--show-toplevel", "--absolute-git-dir")
    lines = out.decode("utf-8", "replace").splitlines() if out else []
    if len(lines) != 2 or not all(lines):
        return None
    return Path(lines[0]), Path(lines[1])


def status(root: Path) -> tuple[str | None, dict[str, list[int] | None]] | None:
    """(HEAD commit, changed Python files) in one git call; see `dirty_python` for the second part.

    Uses `git status --porcelain=v2 --branch` (git 2.11+); falls back to two calls on older git.
    """
    out = _git(
        root,
        "--no-optional-locks",
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=all",
        "--",
        "*.py",
    )
    if out is None:
        dirty = dirty_python(root)
        return None if dirty is None else (head(root), dirty)
    head_commit: str | None = None
    names: list[str] = []
    entries = out.split(b"\0")
    i = 0
    while i < len(entries):
        entry = entries[i].decode("utf-8", "surrogateescape")
        i += 1
        if entry.startswith("# branch.oid "):
            oid = entry[len("# branch.oid ") :].strip()
            head_commit = None if oid == "(initial)" else oid
        elif entry.startswith("1 "):
            names.append(entry.split(" ", 8)[8])
        elif entry.startswith("2 "):
            names.append(entry.split(" ", 9)[9])
            if i < len(entries) and entries[i]:
                names.append(entries[i].decode("utf-8", "surrogateescape"))  # the old name
            i += 1
        elif entry.startswith("u "):
            names.append(entry.split(" ", 10)[10])
        elif entry.startswith("? "):
            names.append(entry[2:])
    return head_commit, _stamped(root, names)


def _stamped(root: Path, names: list[str]) -> dict[str, list[int] | None]:
    """Path -> [size, mtime_ns], None when the file is gone; secret-looking and too-big files left out."""
    result: dict[str, list[int] | None] = {}
    for rel in names:
        if not _indexable_name(rel):
            continue  # secret-looking files are never indexed, so their changes never make the graph stale
        try:
            st = os.stat(root / rel)
        except (OSError, ValueError):
            result[rel] = None
            continue
        if st.st_size <= MAX_BYTES:  # files too big to index do not make the graph stale either
            result[rel] = [st.st_size, st.st_mtime_ns]
    return result


def common_git_dir(root: Path) -> Path | None:
    """The git folder shared by all worktrees of a repository (where its own hooks live)."""
    found = _text(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if found is None:  # git older than 2.31: the answer may be relative to the working tree
        found = _text(_git(root, "rev-parse", "--git-common-dir"))
    if not found:
        return None
    path = Path(found)
    return path if path.is_absolute() else (root / path).resolve()


def head(root: Path) -> str | None:
    return _text(_git(root, "rev-parse", "--verify", "-q", "HEAD"))


def python_files(root: Path) -> list[Path] | None:
    """None when git cannot list the files (not a repository, or git missing)."""
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py")
    if out is None:
        return None
    found: list[Path] = []
    for raw in sorted(set(out.split(b"\0"))):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        if any(_denied(part) for part in PurePosixPath(rel).parts):
            continue
        path = root / rel
        try:
            st = os.stat(path)
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(st.st_mode) and st.st_size <= MAX_BYTES:
            found.append(path)
    return found


@functools.lru_cache(maxsize=4096)
def _denied(part: str) -> bool:
    return denied(part)


def dirty_python(root: Path) -> dict[str, list[int] | None] | None:
    """Python files git reports as changed, new or deleted -> [size, mtime_ns] (None when deleted).

    None when git cannot tell (not a repository, or git missing). `--no-optional-locks` keeps git from
    rewriting its index while a developer works.
    """
    out = _git(
        root, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "*.py"
    )
    if out is None:
        return None
    names: list[str] = []
    entries = out.split(b"\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code = entry[:2].decode("ascii", "replace")
        names.append(entry[3:].decode("utf-8", "surrogateescape"))
        if "R" in code or "C" in code:  # renamed/copied: the next entry is the old path
            if i < len(entries) and entries[i]:
                names.append(entries[i].decode("utf-8", "surrogateescape"))
            i += 1
    return _stamped(root, names)


def _indexable_name(rel: str) -> bool:
    return not any(_denied(part) for part in PurePosixPath(rel).parts)


def changed_between(root: Path, old: str, new: str) -> list[str] | None:
    """Python files that differ between two commits, or None when git cannot tell (e.g. old is gone)."""
    out = _git(root, "diff", "--name-only", "-z", old, new, "--", "*.py")
    if out is None:
        return None
    return sorted(raw.decode("utf-8", "surrogateescape") for raw in out.split(b"\0") if raw)


def stamps(root: Path, paths: list[Path]) -> dict[str, tuple[int, int]]:
    """Relative POSIX path -> (size, mtime_ns); files that cannot be read are left out."""
    out: dict[str, tuple[int, int]] = {}
    for path in paths:
        try:
            st = path.stat()
        except (OSError, ValueError):
            continue
        out[path.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out

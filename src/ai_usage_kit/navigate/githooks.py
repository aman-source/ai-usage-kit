"""Optional git hooks that refresh the graph right after `git pull`, a branch switch or a rebase.

Each hook gets a marked block that starts `eai navigate update --background` and always succeeds, so git is
never slowed down or blocked. Existing hooks are kept: the block goes right after their `#!` line (before any
`exec`). A hook file that eai creates carries a marker line, so `hooks off` deletes only files eai created.
Hooks folders inside the working tree (tracked, e.g. Husky's `.husky/`) are never touched.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath

from ai_usage_kit.defaults.settings import write_atomic
from ai_usage_kit.navigate import files

HOOKS = ("post-merge", "post-checkout", "post-rewrite")
START = "# >>> ai-usage-kit navigate (eai navigate hooks off removes this) >>>"
END = "# <<< ai-usage-kit navigate <<<"
CREATED = "# created by ai-usage-kit navigate; eai navigate hooks off deletes this file"
SHELLS = ("sh", "bash", "dash", "zsh")
NOT_GIT = "not a git repository (or git is not installed)"


NO_EOL = "# (eai: in the original file the line above was the last one, without a line break)"


def block(eai: str, newline: str = "\n", no_eol: bool = False) -> str:
    path = PureWindowsPath(eai).as_posix() if "\\" in eai else eai
    quoted = '"' + re.sub(r'(["$`\\])', r"\\\1", path) + '"'
    body = [
        START,
        *([NO_EOL] if no_eol else []),
        f"{quoted} navigate update --background >/dev/null 2>&1 || true",
        END,
    ]
    return newline.join(body) + newline


def hooks_dir(root: Path) -> Path | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    found = done.stdout.decode("utf-8", "replace").strip()
    path = Path(found)
    return (path if path.is_absolute() else root / path).resolve()


def tracked_hooks_dir(root: Path, directory: Path) -> bool:
    """True for a hooks folder inside the working tree but outside the git dir (its files are tracked)."""
    gdir = files.git_dir(root)
    inside_tree = _inside(directory, root.resolve())
    inside_git = gdir is not None and _inside(directory, gdir.resolve())
    return inside_tree and not inside_git


def shared_hooks_dir(root: Path, directory: Path) -> bool:
    """True for a hooks folder outside this repository's git folder (core.hooksPath shared by many repos)."""
    common = files.common_git_dir(root)
    return common is None or not _inside(directory, common.resolve())


def install(root: Path, eai: str) -> list[str]:
    top = files.repo_root(root)
    directory = hooks_dir(top) if top else None
    if top is None or directory is None:
        return [f"error: {NOT_GIT}"]
    line = block(eai).splitlines()[1]
    if tracked_hooks_dir(top, directory):
        return [
            f"refused: the hooks folder {directory} is inside the repository (tracked hooks, such as Husky).",
            "Add this line to its post-merge, post-checkout and post-rewrite hooks yourself:",
            line,
        ]
    if shared_hooks_dir(top, directory):
        return [
            f"refused: the hooks folder {directory} is shared (core.hooksPath); other repos use it too.",
            "To refresh graphs in every repository using it, add this line to its post-merge, post-checkout "
            "and "
            "post-rewrite hooks yourself:",
            line,
        ]
    directory.mkdir(parents=True, exist_ok=True)
    return [f"  {name}: {_install_one(directory / name, eai)}" for name in HOOKS]


def remove(root: Path) -> list[str]:
    top = files.repo_root(root)
    directory = hooks_dir(top) if top else None
    if top is None or directory is None:
        return [f"error: {NOT_GIT}"]
    return [f"  {name}: {_remove_one(directory / name)}" for name in HOOKS]


def status(root: Path) -> list[str]:
    top = files.repo_root(root)
    directory = hooks_dir(top) if top else None
    if top is None or directory is None:
        return [f"error: {NOT_GIT}"]
    out = []
    for name in HOOKS:
        text = _read_text(directory / name)
        out.append(f"  {name}: {'on' if text is not None and START in text else 'off'}")
    return out


def _install_one(path: Path, eai: str) -> str:
    try:
        return _install_or_raise(path, eai)
    except OSError as error:
        return f"skipped: cannot write ({error.strerror or error})"


def _install_or_raise(path: Path, eai: str) -> str:
    if path.is_symlink():
        return "skipped: a link to another file; add the eai line yourself if you want it"
    if not path.exists():
        content = "#!/bin/sh\n" + CREATED + "\n" + block(eai)
        _write(path, content.encode("utf-8"), 0o755)
        return "created"
    text = _read_text(path)
    if text is None:
        return "skipped: not a text shell script; eai leaves it alone"
    if START in text:
        return "already there"
    newline = "\r\n" if "\r\n" in text else "\n"
    first, sep, rest = text.partition(newline)
    if not first.startswith("#!"):
        return "skipped: no #! line; add the eai line yourself if you want it"
    if _interpreter(first) not in SHELLS:
        return "skipped: not a shell script; eai leaves it alone"
    content = first + newline + block(eai, newline, no_eol=not sep) + rest
    _write(path, content.encode("utf-8"), _mode(path))
    return "added"


def _remove_one(path: Path) -> str:
    try:
        return _remove_or_raise(path)
    except OSError as error:
        return f"not removed: cannot write ({error.strerror or error})"


def _remove_or_raise(path: Path) -> str:
    if path.is_symlink():
        return "not there"
    text = _read_text(path)
    if text is None or START not in text:
        return "not there"
    newline = "\r\n" if "\r\n" in text else "\n"
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END) + r"(\r?\n)?", re.S)
    found = pattern.search(text)
    if found is None:
        return "not there"
    before, after = text[: found.start()], text[found.end() :]
    if NO_EOL in found.group(0) and before.endswith(newline):
        before = before[: -len(newline)]  # the original first line had no line break
    remaining = before + after
    if remaining in (f"#!/bin/sh{newline}{CREATED}{newline}", f"#!/bin/sh{newline}{CREATED}"):
        path.unlink()
        return "removed (file deleted: eai created it)"
    _write(path, remaining.encode("utf-8"), _mode(path))
    return "removed"


def _interpreter(shebang: str) -> str:
    parts = shebang[2:].strip().split()
    if not parts:
        return ""
    name = PurePosixPath(parts[0]).name
    if name == "env":
        args = [p for p in parts[1:] if not p.startswith("-")]
        return PurePosixPath(args[0]).name if args else ""
    return name


def _read_text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _mode(path: Path) -> int:
    try:
        return path.stat().st_mode & 0o7777
    except OSError:
        return 0o755


def _write(path: Path, data: bytes, mode: int) -> None:
    write_atomic(path, data)
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def _inside(path: Path, folder: Path) -> bool:
    a, b = os.path.normcase(str(path.resolve())), os.path.normcase(str(folder))
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)

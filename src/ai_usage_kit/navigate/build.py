"""Build the graph of one working tree under a lock and publish it by pointer file.

Never raises: `update` returns "built", "fresh", "locked", "no-git" or "failed". A failed build keeps the
previous graph. Readers open the file named in `current`; a new graph gets a new file name, so a file that a
reader holds open is never replaced (Windows), only deleted later when no longer in use.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ai_usage_kit.defaults.settings import write_atomic
from ai_usage_kit.navigate import files, snapshot, store
from ai_usage_kit.navigate.graph.config import Config, RepoConfig
from ai_usage_kit.navigate.graph.engine.index import Index

LOCK_MAX_AGE = 1800.0
HEARTBEAT_EVERY = 15.0  # a running build touches its lock this often
HEARTBEAT_STALE = 90.0  # a lock not touched for this long belongs to a build that is gone
_DB_NAME = re.compile(r"graph-\d+\.db")
_STILL_ACTIVE = 259


def update(root: Path, environ: Mapping[str, str], home: Path, now: float | None = None) -> str:
    started_at = time.time() if now is None else now
    info = files.repo_info(root)
    if info is None:
        return "no-git"
    top, gdir = info
    try:
        state = store.graph_dir(environ, home, gdir, top)
    except OSError:
        return "failed"
    store.prune(environ, home, started_at)
    lock = state / "build.lock"
    if not _acquire(lock, started_at):
        return "locked"
    beating = threading.Event()
    heart = threading.Thread(target=_heartbeat, args=(lock, beating), daemon=True)
    heart.start()
    try:
        return _build(top, state)
    except Exception as error:  # fail open: keep the previous graph
        _log(state, f"build failed: {type(error).__name__}")
        return "failed"
    finally:
        beating.set()
        heart.join(timeout=5)
        lock.unlink(missing_ok=True)


def _heartbeat(lock: Path, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_EVERY):
        with contextlib.suppress(OSError):
            os.utime(lock)


def _build(root: Path, state: Path) -> str:
    found = files.status(root)  # before reading any file: later edits then show as stale
    paths = files.python_files(root)
    if paths is None or found is None:
        return "no-git"
    head, dirty = found
    stamps = {k: list(v) for k, v in files.stamps(root, paths).items()}
    current = current_db(state)
    if current is not None:
        meta_now = snapshot.read_meta(current)
        same = meta_now is not None and meta_now.get("head") == head and meta_now.get("files") == stamps
        if same and meta_now is not None and meta_now.get("dirty") == dirty:
            return "fresh"
    started = time.monotonic()
    cfg = Config(repos=[RepoConfig(root.name, root)])
    idx = Index(cfg).build(files={root.name: paths})
    if not state.is_dir():
        return "failed"  # the graph folder was removed meanwhile (eai off navigate, eai uninstall)
    from ai_usage_kit import __version__

    meta = {
        "head": head,
        "files": stamps,
        "dirty": dirty,
        "skipped": len(paths) - len(idx.files),
        "indexed": len(idx.files),
        "seconds": round(time.monotonic() - started, 2),
        "built_at": time.time(),
        "tool_version": __version__,
    }
    target = state / f"graph-{time.time_ns()}.db"
    snapshot.write_db(target, snapshot.dump(idx), meta)
    _publish(state, target)
    _cleanup(state, keep={target.name, current.name if current else ""})
    _log(state, f"built {meta['indexed']} files, {meta['skipped']} skipped, {meta['seconds']} s")
    return "built"


def current_db(state: Path) -> Path | None:
    try:
        name = (state / "current").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not _DB_NAME.fullmatch(name):
        return None
    path = state / name
    return path if path.is_file() else None


def start_background(root: Path) -> bool:
    """Start `python -P -m ai_usage_kit navigate update --repo ROOT` detached; True if it started.

    `-P` and a neutral working folder keep Python from importing modules of the repository being indexed
    (a repo file named json.py must never run).
    """
    cmd = [sys.executable, "-P", "-m", "ai_usage_kit", "navigate", "update", "--repo", str(root)]
    common: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": tempfile.gettempdir(),
    }
    if os.name == "nt":
        # A hidden console (no window flash, also for git), its own process group, and, when allowed, out of
        # the caller's job so the build survives the command that started it.
        base = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        for flags in (base | subprocess.CREATE_BREAKAWAY_FROM_JOB, base):
            try:
                subprocess.Popen(cmd, creationflags=flags, **common)  # type: ignore[call-overload]
                return True
            except OSError:
                continue
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True, **common)  # type: ignore[call-overload]
    except OSError:
        return False
    return True


def _acquire(lock: Path, now: float) -> bool:
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not _stale(lock, now):
                return False
            try:
                lock.unlink()
            except OSError:
                return False
            continue
        except OSError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {now}")
        return True
    return False


def _stale(lock: Path, now: float) -> bool:
    try:
        if time.time() - lock.stat().st_mtime > HEARTBEAT_STALE:
            return True  # no heartbeat: the build is gone even if its process id was reused
    except OSError:
        return True
    try:
        pid_text, stamp_text = lock.read_text(encoding="utf-8").split()
        pid, stamp = int(pid_text), float(stamp_text)
    except (OSError, ValueError):
        return True
    return pid <= 0 or now - stamp > LOCK_MAX_AGE or not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - windll exists only on Windows
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return bool(kernel32.GetLastError() == 5)  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _publish(state: Path, target: Path) -> None:
    for attempt in range(20):
        try:
            write_atomic(state / "current", target.name.encode("utf-8"))
            return
        except PermissionError:  # a reader has `current` open for a moment (Windows)
            if attempt == 19:
                raise
            time.sleep(0.05)


def _cleanup(state: Path, keep: set[str]) -> None:
    for path in state.glob("graph-*.db"):
        if path.name not in keep:
            with contextlib.suppress(OSError):  # still open somewhere; the next build retries
                path.unlink()
    for temp in state.glob(".graph-*.tmp"):
        try:
            if time.time() - temp.stat().st_mtime > 3600:
                temp.unlink()
        except OSError:
            pass


def _log(state: Path, message: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    store.write_private_text(state / "build.log", f"{stamp} {message}\n")

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_usage_kit.navigate import build, files, freshness, snapshot, store
from ai_usage_kit.navigate.graph.engine.index import Index
from tests.navigate.conftest import git, init_repo

DAY = 86400.0


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {"EAI_STATE_DIR": str(tmp_path / "state")}


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text(
        "from pkg.a import f\n\n\ndef g():\n    return f()\n", encoding="utf-8"
    )
    init_repo(root)
    return root


def _state(root: Path, env: dict[str, str], home: Path) -> Path:
    gdir = files.git_dir(root)
    assert gdir is not None
    return store.graph_dir(env, home, gdir, root)


def _dbs(state: Path) -> list[str]:
    return sorted(p.name for p in state.glob("graph-*.db"))


def _touch_edit(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))


# ---------------------------------------------------------------- store


def test_graph_dir_is_outside_the_repo_and_per_clone(tmp_path: Path, env: dict[str, str]) -> None:
    a = _repo(tmp_path, "a")
    b = tmp_path / "b"
    subprocess.run(["git", "clone", "-q", str(a), str(b)], check=True, capture_output=True)
    sa, sb = _state(a, env, tmp_path), _state(b, env, tmp_path)
    assert sa != sb
    assert str(tmp_path / "state" / "navigate" / "graphs") in str(sa)
    assert (sa / "root.txt").read_text(encoding="utf-8") == str(a)


def test_worktree_gets_its_own_graph(tmp_path: Path, env: dict[str, str]) -> None:
    a = _repo(tmp_path, "a")
    wt = tmp_path / "wt"
    git(a, "worktree", "add", "-q", str(wt))
    assert _state(a, env, tmp_path) != _state(wt, env, tmp_path)


def test_build_writes_nothing_inside_the_repo(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".git" not in p.parts)
    git_before = sorted(p.relative_to(root).as_posix() for p in (root / ".git").rglob("*"))
    assert build.update(root, env, tmp_path) == "built"
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".git" not in p.parts) == before
    assert sorted(p.relative_to(root).as_posix() for p in (root / ".git").rglob("*")) == git_before


def test_prune_removes_orphans_and_old_graphs(tmp_path: Path, env: dict[str, str]) -> None:
    live = _repo(tmp_path, "live")
    gone = _repo(tmp_path, "gone")
    old = _repo(tmp_path, "old")
    for root in (live, gone, old):
        assert build.update(root, env, tmp_path) == "built"
    s_live, s_gone, s_old = (_state(r, env, tmp_path) for r in (live, gone, old))
    gone.rename(tmp_path / "moved-away")  # its recorded path no longer exists
    long_ago = time.time() - 100 * DAY
    for p in s_old.iterdir():
        os.utime(p, (long_ago, long_ago))
    assert store.prune(env, tmp_path, time.time()) == 2
    assert s_live.exists() and not s_old.exists() and not s_gone.exists()


def test_remove_all(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    build.update(root, env, tmp_path)
    store.remove_all(env, tmp_path)
    assert not store.graphs_root(env, tmp_path).exists()


# ---------------------------------------------------------------- build


def test_first_build_then_fresh(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    assert build.update(root, env, tmp_path) == "built"
    state = _state(root, env, tmp_path)
    current = build.current_db(state)
    assert current is not None and current.exists()
    meta = snapshot.read_meta(current)
    assert meta is not None
    assert meta["head"] == git(root, "rev-parse", "HEAD")
    assert set(meta["files"]) == {"pkg/__init__.py", "pkg/a.py", "pkg/b.py"}
    assert meta["skipped"] == 0 and "seconds" in meta and "tool_version" in meta
    assert build.update(root, env, tmp_path) == "fresh"
    assert _dbs(state) == [current.name]


def test_edit_rebuilds_and_old_graphs_are_cleaned_even_if_open(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    build.update(root, env, tmp_path)
    state = _state(root, env, tmp_path)
    first = build.current_db(state)
    assert first is not None
    reader = sqlite3.connect(first.resolve().as_uri() + "?mode=ro", uri=True)
    reader.execute("SELECT count(*) FROM sections").fetchone()
    _touch_edit(root / "pkg" / "a.py", "def f():\n    return 22\n")
    assert build.update(root, env, tmp_path) == "built"
    second = build.current_db(state)
    assert second is not None and second != first
    reader.close()
    _touch_edit(root / "pkg" / "a.py", "def f():\n    return 333\n")
    assert build.update(root, env, tmp_path) == "built"
    assert not first.exists()
    assert len(_dbs(state)) <= 2


def test_lock_held_by_live_process(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    state = _state(root, env, tmp_path)
    (state / "build.lock").write_text(f"{os.getpid()} {time.time()}", encoding="utf-8")
    assert build.update(root, env, tmp_path) == "locked"
    assert build.current_db(state) is None


def test_lock_of_dead_process_is_replaced(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    state = _state(root, env, tmp_path)
    done = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True
    )
    dead = int(done.stdout.strip())
    (state / "build.lock").write_text(f"{dead} {time.time()}", encoding="utf-8")
    assert build.update(root, env, tmp_path) == "built"
    assert not (state / "build.lock").exists()


@pytest.mark.parametrize("content", ["garbage", f"{os.getpid()} {time.time() - 2000}"])
def test_old_or_unreadable_lock_is_replaced(tmp_path: Path, env: dict[str, str], content: str) -> None:
    root = _repo(tmp_path)
    state = _state(root, env, tmp_path)
    (state / "build.lock").write_text(content, encoding="utf-8")
    assert build.update(root, env, tmp_path) == "built"


def test_not_a_repo(tmp_path: Path, env: dict[str, str]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert build.update(plain, env, tmp_path) == "no-git"


def test_syntax_error_is_skipped(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    (root / "pkg" / "broken.py").write_text("def f(:\n", encoding="utf-8")
    assert build.update(root, env, tmp_path) == "built"
    current = build.current_db(_state(root, env, tmp_path))
    assert current is not None
    meta = snapshot.read_meta(current)
    assert meta is not None and meta["skipped"] == 1


def test_failed_build_keeps_previous_graph(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    build.update(root, env, tmp_path)
    state = _state(root, env, tmp_path)
    before = build.current_db(state)
    _touch_edit(root / "pkg" / "a.py", "def f():\n    return 4\n")

    def boom(self: Index, files: object = None) -> Index:
        raise RuntimeError("secret source text must not be logged")

    monkeypatch.setattr(Index, "build", boom)
    assert build.update(root, env, tmp_path) == "failed"
    assert build.current_db(state) == before
    log = (state / "build.log").read_text(encoding="utf-8")
    assert "RuntimeError" in log and "secret source" not in log
    assert not (state / "build.lock").exists()


def test_corrupt_current_graph_is_rebuilt(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    build.update(root, env, tmp_path)
    state = _state(root, env, tmp_path)
    current = build.current_db(state)
    assert current is not None
    current.write_bytes(b"junk")
    assert build.update(root, env, tmp_path) == "built"


def test_pointer_to_missing_file(tmp_path: Path, env: dict[str, str]) -> None:
    state = tmp_path / "s"
    state.mkdir()
    (state / "current").write_text("graph-1.db", encoding="utf-8")
    assert build.current_db(state) is None
    (state / "current").write_text("../evil.db", encoding="utf-8")
    assert build.current_db(state) is None


def test_background_build(tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EAI_STATE_DIR", env["EAI_STATE_DIR"])
    root = _repo(tmp_path)
    assert build.start_background(root)
    state = _state(root, env, Path.home())
    deadline = time.time() + 60
    while time.time() < deadline and build.current_db(state) is None:
        time.sleep(0.25)
    assert build.current_db(state) is not None


def test_background_build_never_imports_repository_code(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EAI_STATE_DIR", env["EAI_STATE_DIR"])
    root = _repo(tmp_path)
    marker = tmp_path / "ran.txt"
    for name in ("json.py", "typing.py", "sqlite3.py", "zlib.py"):
        (root / name).write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    assert build.start_background(root)
    state = _state(root, env, Path.home())
    deadline = time.time() + 60
    while time.time() < deadline and build.current_db(state) is None:
        time.sleep(0.25)
    assert build.current_db(state) is not None
    assert not marker.exists()


def test_build_does_not_rewrite_the_git_index(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    index = root / ".git" / "index"
    before = index.stat().st_mtime_ns
    later = time.time() + 30
    os.utime(root / "pkg" / "a.py", (later, later))  # same content, newer time: git status would refresh
    time.sleep(0.05)
    assert build.update(root, env, tmp_path) == "built"
    assert index.stat().st_mtime_ns == before


def test_secret_or_huge_dirty_files_do_not_make_the_graph_stale(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    meta = _meta(root, env, tmp_path)
    (root / "pkg" / "my_secret.py").write_text("TOKEN = 'x'\n", encoding="utf-8")
    (root / "pkg" / "huge.py").write_bytes(b"#" * (files.MAX_BYTES + 1))
    fresh = freshness.check(root, meta)
    assert not fresh.stale and fresh.changed == []
    assert build.update(root, env, tmp_path) == "fresh"


def test_build_stops_if_its_folder_is_removed_meanwhile(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    state = _state(root, env, tmp_path)
    real = Index.build

    def removing(self: Index, files: object = None) -> Index:
        import shutil

        shutil.rmtree(state)  # what eai uninstall does
        return real(self, files)  # type: ignore[arg-type]

    monkeypatch.setattr(Index, "build", removing)
    assert build.update(root, env, tmp_path) == "failed"
    assert not state.exists()


def test_lock_with_live_pid_but_no_heartbeat_is_replaced(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    state = _state(root, env, tmp_path)
    lock = state / "build.lock"
    lock.write_text(f"{os.getpid()} {time.time()}", encoding="utf-8")
    old = time.time() - (build.HEARTBEAT_STALE + 5)
    os.utime(lock, (old, old))  # a reused pid would look alive; the lock was not touched
    assert build.update(root, env, tmp_path) == "built"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_graph_files_are_private(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    build.update(root, env, tmp_path)
    state = _state(root, env, tmp_path)
    for path in state.iterdir():
        assert path.stat().st_mode & 0o077 == 0, path


# ---------------------------------------------------------------- freshness


def _meta(root: Path, env: dict[str, str], home: Path) -> dict[str, object]:
    build.update(root, env, home)
    current = build.current_db(_state(root, env, home))
    assert current is not None
    meta = snapshot.read_meta(current)
    assert meta is not None
    return meta


def test_freshness_unchanged(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    fresh = freshness.check(root, _meta(root, env, tmp_path))
    assert not fresh.stale and fresh.changed == [] and fresh.indexed is not None and len(fresh.indexed) == 7


def test_freshness_after_commit_edit_add_delete(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    meta = _meta(root, env, tmp_path)
    _touch_edit(root / "pkg" / "a.py", "def f():\n    return 5\n")
    (root / "pkg" / "new.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pkg" / "b.py").unlink()
    fresh = freshness.check(root, meta)
    assert fresh.stale and fresh.changed == ["pkg/a.py", "pkg/b.py", "pkg/new.py"]


def test_freshness_after_commit_only(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    meta = _meta(root, env, tmp_path)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "docs")
    fresh = freshness.check(root, meta)
    assert fresh.stale and fresh.changed == []


def test_freshness_after_branch_switch(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    git(root, "checkout", "-q", "-b", "other")
    _touch_edit(root / "pkg" / "a.py", "def f():\n    return 6\n")
    git(root, "commit", "-q", "-am", "other")
    meta = _meta(root, env, tmp_path)
    git(root, "checkout", "-q", "-")
    fresh = freshness.check(root, meta)
    assert fresh.stale and "pkg/a.py" in fresh.changed


def test_freshness_many_changes_listed_in_full(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    meta = _meta(root, env, tmp_path)
    for i in range(25):
        (root / "pkg" / f"n{i:02}.py").write_text("x = 1\n", encoding="utf-8")
    assert len(freshness.check(root, meta).changed) == 25


def test_freshness_with_files_already_uncommitted_at_build(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    _touch_edit(root / "pkg" / "a.py", "def f():\n    return 7\n")
    (root / "pkg" / "draft.py").write_text("y = 1\n", encoding="utf-8")
    meta = _meta(root, env, tmp_path)
    assert not freshness.check(root, meta).stale  # same uncommitted state as at build time
    later = time.time() + 20
    (root / "pkg" / "draft.py").write_text("y = 22\n", encoding="utf-8")
    os.utime(root / "pkg" / "draft.py", (later, later))
    fresh = freshness.check(root, meta)
    assert fresh.stale and fresh.changed == ["pkg/draft.py"]


def test_freshness_after_reverting_a_change(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    _touch_edit(root / "pkg" / "a.py", "def f():\n    return 8\n")
    meta = _meta(root, env, tmp_path)
    git(root, "checkout", "--", "pkg/a.py")
    fresh = freshness.check(root, meta)
    assert fresh.stale and fresh.changed == ["pkg/a.py"]


def test_freshness_ignores_non_python_changes(tmp_path: Path, env: dict[str, str]) -> None:
    root = _repo(tmp_path)
    meta = _meta(root, env, tmp_path)
    (root / "notes.txt").write_text("x\n", encoding="utf-8")
    assert not freshness.check(root, meta).stale


def test_freshness_without_git(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    fresh = freshness.check(plain, {"head": "abc", "files": {}})
    assert fresh.stale

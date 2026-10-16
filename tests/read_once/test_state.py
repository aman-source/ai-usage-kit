import os
import time
from pathlib import Path

import pytest

from ai_usage_kit.hook_files import append_count, read_counts, safe_id, write_private
from ai_usage_kit.read_once import state


def test_safe_id() -> None:
    assert safe_id("../../etc", "x") == "etc"
    assert safe_id(None, "fallback") == "fallback"
    assert safe_id("abc-DEF_1", "x") == "abc-DEF_1"


def test_write_private_is_atomic_and_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: dict[str, int] = {}
    real = os.chmod

    def record(path: str | Path, mode: int) -> None:
        modes[Path(path).name] = mode
        real(path, mode)

    monkeypatch.setattr("os.chmod", record)
    target = tmp_path / "d" / "f.json"
    write_private(target, "one")
    write_private(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert modes["f.json"] == 0o600 and modes["d"] == 0o700
    assert [p.name for p in target.parent.iterdir()] == ["f.json"]


def test_counts(tmp_path: Path) -> None:
    path = tmp_path / "counts.jsonl"
    append_count(path, {"day": "2999-01-01", "before": 10, "after": 0})
    path.write_text(path.read_text(encoding="utf-8") + '{"day": "2999-01-01", "before": "x"}\n[1]\n', "utf-8")
    assert read_counts(path, time.time()) == (1, 10, 0)
    assert read_counts(tmp_path / "missing.jsonl", time.time()) == (0, 0, 0)


def test_paths() -> None:
    base = Path("S")
    assert state.agent_dir(base, "sess", None) == base / "read-once" / "sess" / "main"
    assert state.record_file(base, "sess", "a1", "k").parent == base / "read-once" / "sess" / "a1"
    assert state.record_file(base, "s", None, "k1") != state.record_file(base, "s", None, "k2")
    pending = state.pending_file(base, "s", None, "toolu_1")
    assert pending == base / "read-once" / "s" / "main" / "pending" / "toolu_1.json"
    assert state.agent_dir(base, "s", "../x") == base / "read-once" / "s" / "x"


def test_record_round_trip(tmp_path: Path) -> None:
    path = state.record_file(tmp_path, "sess", None, "k")
    entry = {"size": 1, "mtime": 2, "sha256": "h", "at": 3.0, "denied": False}
    state.save(path, entry)
    assert state.load_record(path) == entry
    pending = state.pending_file(tmp_path, "sess", None, "t")
    state.save(pending, {"size": 1, "mtime": 2, "sha256": "h"})
    assert state.load_pending(pending) == {"size": 1, "mtime": 2, "sha256": "h"}


@pytest.mark.parametrize("text", ["", "{", "[1]", '{"size": "x"}', '{"size": 1}', "\x00\xff"])
def test_corrupt_files_load_as_none(tmp_path: Path, text: str) -> None:
    path = tmp_path / "s.json"
    path.write_text(text, encoding="utf-8", errors="ignore")
    assert state.load_record(path) is None
    assert state.load_pending(path) is None


def test_forget_session_and_expire(tmp_path: Path) -> None:
    keep = state.record_file(tmp_path, "other", None, "k")
    state.save(keep, {"size": 1, "mtime": 1, "sha256": "h", "at": 1.0, "denied": False})
    gone = state.record_file(tmp_path, "sess", "agent-1", "k")
    state.save(gone, {"size": 1, "mtime": 1, "sha256": "h", "at": 1.0, "denied": False})
    state.forget_session(tmp_path, "sess")
    assert not (tmp_path / "read-once" / "sess").exists() and keep.exists()
    old = time.time() - (state.KEEP_DAYS + 1) * 86400
    os.utime(keep, (old, old))
    state.expire(tmp_path, time.time())
    assert not keep.exists()
    assert not (tmp_path / "read-once" / "other").exists()

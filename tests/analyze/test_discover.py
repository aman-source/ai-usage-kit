import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_usage_kit.analyze.discover import SessionFiles, discover
from tests.analyze.records import prompt, write_jsonl


def test_finds_main_and_subagent_files(tmp_path: Path) -> None:
    proj = tmp_path / "C--work-repo"
    write_jsonl(proj / "s1.jsonl", [prompt("a")])
    write_jsonl(proj / "s1" / "subagents" / "agent-1.jsonl", [prompt("b")])
    write_jsonl(proj / "s2" / "subagents" / "agent-2.jsonl", [prompt("c")])
    (proj / "notes").mkdir()
    found = discover(tmp_path)
    assert [(f.project, f.session_id, f.main is not None, len(f.subagents)) for f in found] == [
        ("C--work-repo", "s1", True, 1),
        ("C--work-repo", "s2", False, 1),
    ]
    assert found[0].paths() == (proj / "s1.jsonl", proj / "s1" / "subagents" / "agent-1.jsonl")


def test_project_filter_is_case_insensitive_substring(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "C--work-Alpha" / "a.jsonl", [prompt("a")])
    write_jsonl(tmp_path / "C--work-beta" / "b.jsonl", [prompt("b")])
    assert [f.session_id for f in discover(tmp_path, project="alpha")] == ["a"]


def test_since_drops_sessions_last_modified_before_cutoff(tmp_path: Path) -> None:
    old = write_jsonl(tmp_path / "p" / "old.jsonl", [prompt("a")])
    write_jsonl(tmp_path / "p" / "new.jsonl", [prompt("b")])
    forty_days_ago = time.time() - 40 * 86400
    os.utime(old, (forty_days_ago, forty_days_ago))
    since = datetime.now(UTC) - timedelta(days=30)
    assert [f.session_id for f in discover(tmp_path, since=since)] == ["new"]


def test_missing_root_finds_nothing(tmp_path: Path) -> None:
    assert discover(tmp_path / "nope") == []


def test_unreadable_project_dir_is_reported_and_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_jsonl(tmp_path / "C--locked" / "a.jsonl", [prompt("a")])
    write_jsonl(tmp_path / "C--open" / "b.jsonl", [prompt("b")])
    real_iterdir = Path.iterdir

    def iterdir(self: Path) -> Iterator[Path]:
        if self.name == "C--locked":
            raise PermissionError(self)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    unreadable: list[Path] = []
    assert [f.session_id for f in discover(tmp_path, unreadable=unreadable)] == ["b"]
    assert unreadable == [tmp_path / "C--locked"]


def test_file_vanishing_before_stat_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = write_jsonl(tmp_path / "p" / "gone.jsonl", [prompt("a")])
    write_jsonl(tmp_path / "p" / "kept.jsonl", [prompt("b")])
    real = SessionFiles.last_modified

    def last_modified(self: SessionFiles) -> datetime:
        if self.session_id == "gone":
            raise FileNotFoundError(self.main)
        return real(self)

    monkeypatch.setattr(SessionFiles, "last_modified", last_modified)
    unreadable: list[Path] = []
    since = datetime.now(UTC) - timedelta(days=1)
    assert [f.session_id for f in discover(tmp_path, since=since, unreadable=unreadable)] == ["kept"]
    assert unreadable == [gone]

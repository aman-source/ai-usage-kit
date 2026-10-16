"""Tests for the final-review findings on the read-once branch."""

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.hook_files import append_count, prune_counts, read_counts
from ai_usage_kit.read_once import state
from ai_usage_kit.read_once.hook import handle

NOW = time.time()
EXE = "C:\\Users\\dev\\.local\\bin\\eai.exe"


def _read(event: str, path: Path, tool_use_id: str, agent: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": "s", "hook_event_name": event, "tool_name": "Read", "tool_use_id": tool_use_id,
        "cwd": str(path.parent), "tool_input": {"file_path": str(path)},
    }
    if agent:
        payload["agent_id"] = agent
    return payload


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "server.log"
    path.write_text("started\n" * 100, encoding="utf-8")
    return path


# Critical 1: only content Claude actually saw is recorded.
def test_file_changed_during_the_read_is_not_recorded(tmp_path: Path, log: Path) -> None:
    assert handle(_read("PreToolUse", log, "t1"), tmp_path, NOW) is None
    with log.open("a", encoding="utf-8") as handle_:
        handle_.write("ERROR: crashed\n")  # written after Claude's Read, before the record
    handle(_read("PostToolUse", log, "t1"), tmp_path, NOW)
    assert handle(_read("PreToolUse", log, "t2"), tmp_path, NOW) is None


def test_unchanged_file_is_recorded_and_denied_next_time(tmp_path: Path, log: Path) -> None:
    handle(_read("PreToolUse", log, "t1"), tmp_path, NOW)
    handle(_read("PostToolUse", log, "t1"), tmp_path, NOW)
    assert handle(_read("PreToolUse", log, "t2"), tmp_path, NOW) is not None


def test_post_without_matching_pre_records_nothing(tmp_path: Path, log: Path) -> None:
    handle(_read("PostToolUse", log, "orphan"), tmp_path, NOW)
    assert handle(_read("PreToolUse", log, "t2"), tmp_path, NOW) is None


# Important 2: a parallel read can never undo a denial (each read key has its own file).
def test_parallel_reads_do_not_touch_each_others_records(tmp_path: Path, log: Path) -> None:
    other = tmp_path / "other.txt"
    other.write_text("x\n" * 100, encoding="utf-8")
    for path, tid in ((log, "a"), (other, "b")):
        handle(_read("PreToolUse", path, tid), tmp_path, NOW)
        handle(_read("PostToolUse", path, tid), tmp_path, NOW)
    handle(_read("PreToolUse", other, "b2"), tmp_path, NOW)  # Claude reads both in one turn
    assert handle(_read("PreToolUse", log, "a2"), tmp_path, NOW) is not None  # denied once
    handle(_read("PostToolUse", other, "b2"), tmp_path, NOW)  # the parallel read finishes later
    assert handle(_read("PreToolUse", log, "a3"), tmp_path, NOW) is None  # retry still allowed


# Important 3: the reason tells Claude what to do if the earlier result is gone (rewind, clearing).
def test_reason_covers_content_no_longer_visible(tmp_path: Path, log: Path) -> None:
    handle(_read("PreToolUse", log, "t1"), tmp_path, NOW)
    handle(_read("PostToolUse", log, "t1"), tmp_path, NOW)
    out = handle(_read("PreToolUse", log, "t2"), tmp_path, NOW)
    assert out is not None
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "If it is not" in reason and "rewound" in reason and "read it again now" in reason


def _ctx(tmp_path: Path) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", {}, Managed(), tmp_path / "proj", eai_path=EXE)


def _settings(ctx: Context) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    return data


# Important 4: turning both hook items off leaves no empty lists behind.
def test_both_hook_items_off_restores_the_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text('{"model": "x"}', encoding="utf-8")
    for item in ("trim", "read-once"):
        turn_on(ctx, item)
    for item in ("trim", "read-once"):
        turn_off(ctx, item)
    assert _settings(ctx) == {"model": "x"}
    uninstall(ctx)
    assert ctx.settings_path.read_text(encoding="utf-8") == '{"model": "x"}'


# Important 5: restoring one event through init keeps what eai created.
def test_restore_then_off_leaves_no_empty_lists(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text('{"model": "x"}', encoding="utf-8")
    turn_on(ctx, "read-once")
    data = _settings(ctx)
    del data["hooks"]["SessionStart"]
    ctx.settings_path.write_text(json.dumps(data), encoding="utf-8")
    init(ctx)
    turn_off(ctx, "read-once")
    assert "hooks" not in _settings(ctx)


# Minors.
def test_status_counts_file_sizes_in_bytes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "read-once")
    row: dict[str, object] = {"day": "2999-01-01", "before": 4000, "after": 0}
    append_count(ctx.state_dir / "read-once" / "counts.jsonl", row)
    line = next(line for line in status(ctx).lines if line.startswith("read-once skipped"))
    assert "4,000 bytes not re-sent" in line


def test_counts_are_pruned_after_90_days(tmp_path: Path) -> None:
    path = tmp_path / "counts.jsonl"
    path.write_text(
        '{"day": "2000-01-01", "before": 1, "after": 0}\n{"day": "2999-01-01", "before": 2, "after": 0}\n',
        encoding="utf-8",
    )
    prune_counts(path, NOW)
    assert read_counts(path, NOW, days=3650) == (1, 2, 0)


def test_old_hook_record_format_does_not_crash_status(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.state_dir.mkdir(parents=True)
    old = {"key": "hooks.PostToolUse", "hook_command": "x", "added": [], "created": True}
    ctx.state_path.write_text(json.dumps({"version": 1, "applied": {"trim": old}, "off": []}), "utf-8")
    assert status(ctx).code == 0


def test_pending_records_expire(tmp_path: Path, log: Path) -> None:
    handle(_read("PreToolUse", log, "never-finished"), tmp_path, NOW)
    pending = list((tmp_path / "read-once").rglob("pending/*.json"))
    assert len(pending) == 1
    old = NOW - (state.KEEP_DAYS + 1) * 86400
    os.utime(pending[0], (old, old))
    state.expire(tmp_path, NOW)
    assert not pending[0].exists()


def test_same_file_written_differently_is_one_file(tmp_path: Path, log: Path) -> None:
    first = _read("PreToolUse", log, "t1")
    handle(first, tmp_path, NOW)
    handle({**first, "hook_event_name": "PostToolUse"}, tmp_path, NOW)
    variants = [str(log).replace("\\", "/"), "server.log"]
    if os.name == "nt":
        variants.append(str(log).upper())
    for n, variant in enumerate(variants):
        payload = _read("PreToolUse", log, f"v{n}")
        payload["tool_input"]["file_path"] = variant
        assert handle(payload, tmp_path, NOW) is not None, variant
        key = json.dumps([os.path.normcase(str(log)), None, None, None])
        record = state.record_file(tmp_path, "s", None, key)
        entry = json.loads(record.read_text(encoding="utf-8"))
        entry["denied"] = False  # reset so each spelling can be checked
        record.write_text(json.dumps(entry), encoding="utf-8")

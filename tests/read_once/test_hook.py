import json
import time
from pathlib import Path
from typing import Any

import pytest

from ai_usage_kit.cli import main
from ai_usage_kit.hook_files import read_counts
from ai_usage_kit.read_once import state
from ai_usage_kit.read_once.hook import handle, run_hook

NOW = time.time()


def _read(
    event: str, path: Path, session: str = "s", agent: str | None = None, **tool: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session,
        "hook_event_name": event,
        "tool_name": "Read",
        "cwd": str(path.parent),
        "tool_input": {"file_path": str(path), **tool},
    }
    if agent:
        payload["agent_id"] = agent
    return payload


def _do_read(tmp: Path, path: Path, **kw: Any) -> dict[str, Any] | None:
    """Simulate Claude Code: PreToolUse, then (if allowed) PostToolUse."""
    decision = handle(_read("PreToolUse", path, **kw), tmp, NOW)
    if decision is None:
        handle(_read("PostToolUse", path, **kw), tmp, NOW)
    return decision


@pytest.fixture
def code(tmp_path: Path) -> Path:
    path = tmp_path / "app.py"
    path.write_text("print('hello')\n" * 50, encoding="utf-8")
    return path


def test_second_unchanged_read_is_denied_once(tmp_path: Path, code: Path) -> None:
    assert _do_read(tmp_path, code) is None
    decision = _do_read(tmp_path, code)
    assert decision is not None
    out = decision["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
    assert "unchanged since you read it" in out["permissionDecisionReason"]
    assert "read it again now and it will be allowed" in out["permissionDecisionReason"]
    assert _do_read(tmp_path, code) is None  # retry valve
    assert read_counts(state.counts_file(tmp_path), NOW)[0] == 1


def test_changed_file_is_allowed(tmp_path: Path, code: Path) -> None:
    _do_read(tmp_path, code)
    code.write_text("print('changed')\n" * 50, encoding="utf-8")
    assert _do_read(tmp_path, code) is None


def test_touched_file_is_allowed(tmp_path: Path, code: Path) -> None:
    import os

    _do_read(tmp_path, code)
    os.utime(code, (NOW + 10, NOW + 10))
    assert _do_read(tmp_path, code) is None


@pytest.mark.parametrize("other", [{"offset": 10}, {"limit": 5}, {"pages": "1-2"}])
def test_other_range_is_allowed(tmp_path: Path, code: Path, other: dict[str, Any]) -> None:
    _do_read(tmp_path, code)
    assert _do_read(tmp_path, code, **other) is None


def test_agents_do_not_block_each_other(tmp_path: Path, code: Path) -> None:
    _do_read(tmp_path, code)
    assert _do_read(tmp_path, code, agent="sub-1") is None
    assert _do_read(tmp_path, code, agent="sub-2") is None
    assert _do_read(tmp_path, code, agent="sub-1") is not None


def test_sessions_do_not_block_each_other(tmp_path: Path, code: Path) -> None:
    _do_read(tmp_path, code)
    assert _do_read(tmp_path, code, session="other") is None


@pytest.mark.parametrize("source", ["compact", "clear"])
def test_compaction_and_clear_forget_everything(tmp_path: Path, code: Path, source: str) -> None:
    _do_read(tmp_path, code)
    _do_read(tmp_path, code, agent="sub-1")
    handle({"session_id": "s", "hook_event_name": "SessionStart", "source": source}, tmp_path, NOW)
    assert _do_read(tmp_path, code) is None
    assert _do_read(tmp_path, code, agent="sub-1") is None


def test_startup_and_resume_do_not_forget(tmp_path: Path, code: Path) -> None:
    _do_read(tmp_path, code)
    handle({"session_id": "s", "hook_event_name": "SessionStart", "source": "resume"}, tmp_path, NOW)
    assert _do_read(tmp_path, code) is not None


@pytest.mark.parametrize("name", ["shot.png", "doc.pdf", "nb.ipynb", "pic.JPG"])
def test_media_and_notebooks_are_never_denied(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"x" * 100)
    _do_read(tmp_path, path)
    assert _do_read(tmp_path, path) is None


def test_large_files_are_never_denied(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    _do_read(tmp_path, path)
    assert _do_read(tmp_path, path) is None


def test_missing_file_and_directories_are_allowed(tmp_path: Path) -> None:
    assert _do_read(tmp_path, tmp_path / "nope.py") is None
    assert _do_read(tmp_path, tmp_path) is None


def test_relative_path_uses_cwd(tmp_path: Path, code: Path) -> None:
    for event in ("PreToolUse", "PostToolUse"):
        payload = _read(event, code)
        payload["tool_input"]["file_path"] = "app.py"
        handle(payload, tmp_path, NOW)
    assert _do_read(tmp_path, code) is not None


def test_corrupt_state_never_denies(tmp_path: Path, code: Path) -> None:
    _do_read(tmp_path, code)
    for record in (tmp_path / "read-once" / "s" / "main").glob("*.json"):
        record.write_text("{broken", encoding="utf-8")
    assert _do_read(tmp_path, code) is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash"},
        {"hook_event_name": "PreToolUse", "tool_name": "Read"},
        {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": "x"},
    ],
)
def test_other_inputs_say_nothing(tmp_path: Path, payload: dict[str, Any]) -> None:
    assert handle(payload, tmp_path, NOW) is None


@pytest.mark.parametrize("raw", [b"", b"not json", b"\xff", b"[1]"])
def test_run_hook_never_raises(tmp_path: Path, raw: bytes) -> None:
    assert run_hook(raw, tmp_path, NOW) == b""


def test_no_content_is_stored(tmp_path: Path, code: Path) -> None:
    _do_read(tmp_path, code)
    _do_read(tmp_path, code)
    stored = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "read-once").rglob("*.json*"))
    assert "hello" not in stored


def test_cli_hook(
    tmp_path: Path, code: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    import io
    import sys

    monkeypatch.setenv("EAI_STATE_DIR", str(tmp_path / "st"))
    for event in ("PreToolUse", "PostToolUse", "PreToolUse"):
        raw = json.dumps(_read(event, code)).encode()
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
        assert main(["hook", "read-once"]) == 0
    assert b'"permissionDecision": "deny"' in capsysbinary.readouterr().out

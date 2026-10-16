import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from ai_usage_kit.cli import main
from ai_usage_kit.trim.hook import KEEP_DAYS, handle, read_counts, run_hook, state_dir

NOW = 1_800_000_000.0
PASSING = (
    "=== test session starts ===\ncollected 400 items\n\n"
    + "".join(f"tests/test_m.py::test_{i} PASSED{' ' * 30}[{i // 4:3d}%]\n" for i in range(400))
    + "\n=== 400 passed in 3.21s ===\n"
)


def _payload(command: str = "pytest -v", stdout: str = PASSING, **response: Any) -> dict[str, Any]:
    tool_response = {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False, **response}
    return {
        "session_id": "sess-1",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_use_id": "toolu_01",
        "tool_input": {"command": command},
        "tool_response": tool_response,
    }


def test_trims_a_large_passing_pytest_run(tmp_path: Path) -> None:
    out = handle(_payload(), tmp_path, NOW)
    assert out is not None
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert updated["stdout"].startswith("Pytest: 400 passed in 3.21s")
    assert updated["stderr"] == "" and updated["interrupted"] is False and updated["isImage"] is False
    saved = tmp_path / "trim" / "sess-1" / "toolu_01.log"
    assert f"full output: {saved.as_posix()}]" in updated["stdout"]
    assert "showed 1 of" in updated["stdout"]
    assert PASSING in saved.read_text(encoding="utf-8")


def test_keeps_other_response_fields(tmp_path: Path) -> None:
    out = handle(_payload(returnCodeInterpretation="ok"), tmp_path, NOW)
    assert out is not None
    assert out["hookSpecificOutput"]["updatedToolOutput"]["returnCodeInterpretation"] == "ok"


@pytest.mark.parametrize(
    "payload",
    [
        {**_payload(), "hook_event_name": "PostToolUseFailure"},
        {**_payload(), "tool_name": "PowerShell"},
        _payload(command="pytest -v | tail"),
        _payload(command="git log"),
        _payload(stdout="=== 3 passed in 0.1s ===\n"),
        _payload(interrupted=True),
        _payload(isImage=True),
        {**_payload(), "tool_response": "text"},
        {**_payload(), "tool_response": {"stdout": PASSING}},
        {**_payload(), "tool_input": {}},
    ],
)
def test_leaves_output_alone(tmp_path: Path, payload: dict[str, Any]) -> None:
    assert handle(payload, tmp_path, NOW) is None
    assert not (tmp_path / "trim").exists()


def test_small_saving_is_not_worth_it(tmp_path: Path) -> None:
    lines = "".join(f"line {i} of real build output that matters\n" for i in range(300))
    assert handle(_payload(command="npm run build", stdout=lines), tmp_path, NOW) is None


def test_no_tests_result_never_replaces_real_output(tmp_path: Path) -> None:
    weird = "".join(f"collected item {i} with no summary line at all\n" for i in range(400))
    assert handle(_payload(stdout=weird), tmp_path, NOW) is None


def test_empty_filter_result_never_replaces_output(tmp_path: Path) -> None:
    noise = "".join(f"  Downloading pkg{i}-1.0.whl (1 kB)\n" for i in range(400))
    assert handle(_payload(command="uv sync", stdout=noise), tmp_path, NOW) is None


@pytest.mark.parametrize("raw", [b"", b"not json", b"\xff\xfe", b"[1, 2]", b'{"tool_response": null}'])
def test_run_hook_never_raises_and_says_nothing(tmp_path: Path, raw: bytes) -> None:
    assert run_hook(raw, tmp_path, NOW) == b""


def test_run_hook_returns_json(tmp_path: Path) -> None:
    out = run_hook(json.dumps(_payload()).encode(), tmp_path, NOW)
    stdout = json.loads(out)["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert stdout.startswith("Pytest: 400 passed")


def test_saved_logs_are_private_and_expire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: dict[str, int] = {}
    real_chmod = os.chmod

    def record(path: Any, mode: int) -> None:
        modes[Path(path).name] = mode
        real_chmod(path, mode)

    monkeypatch.setattr("os.chmod", record)
    old = tmp_path / "trim" / "old-session" / "x.log"
    old.parent.mkdir(parents=True)
    old.write_text("old", encoding="utf-8")
    stale = NOW - (KEEP_DAYS + 1) * 86400
    os.utime(old, (stale, stale))
    handle(_payload(), tmp_path, NOW)
    assert modes["toolu_01.log"] == 0o600
    assert modes["sess-1"] == 0o700
    assert not old.exists()
    assert not old.parent.exists()


def test_unsafe_ids_cannot_escape_the_folder(tmp_path: Path) -> None:
    payload = {**_payload(), "session_id": "../../etc", "tool_use_id": "..\\x/y"}
    handle(payload, tmp_path, NOW)
    saved = list((tmp_path / "trim").rglob("*.log"))
    assert len(saved) == 1
    assert saved[0].resolve().is_relative_to((tmp_path / "trim").resolve())


def test_counts_are_numbers_only(tmp_path: Path) -> None:
    handle(_payload(), tmp_path, NOW)
    runs, before, after = read_counts(tmp_path, NOW)
    assert runs == 1 and before == len(PASSING) and 0 < after < before
    text = (tmp_path / "trim" / "counts.jsonl").read_text(encoding="utf-8")
    assert "test_m.py" not in text and "sess-1" not in text
    assert read_counts(tmp_path, NOW + 40 * 86400) == (0, 0, 0)


def test_state_dir_honors_override(tmp_path: Path) -> None:
    assert state_dir({"EAI_STATE_DIR": str(tmp_path / "s")}, tmp_path) == tmp_path / "s"
    assert state_dir({}, tmp_path) == tmp_path / ".ai-usage-kit"


def test_cli_hook_reads_stdin_and_writes_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    import io
    import sys

    monkeypatch.setenv("EAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(_payload()).encode())))
    assert main(["hook", "trim"]) == 0
    assert b"updatedToolOutput" in capsysbinary.readouterr().out


def test_timing_is_quick(tmp_path: Path) -> None:
    started = time.perf_counter()
    handle(_payload(stdout=PASSING * 10), tmp_path, NOW)
    assert time.perf_counter() - started < 1.0

"""Tests for the final-review findings on the trim branch."""

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.trim.filters.gradle import is_trimmable_build
from ai_usage_kit.trim.filters.pytest import filter_pytest
from ai_usage_kit.trim.hook import KEEP_DAYS, expire_logs, handle, read_counts
from ai_usage_kit.trim.install import IF_RULES, hook_command_for, hook_group, is_persistent_install
from ai_usage_kit.trim.matching import PREFIXES, match_command

NOW = 1_800_000_000.0
PASSING = (
    "=== test session starts ===\ncollected 400 items\n\n"
    + "".join(f"tests/test_m.py::test_{i} PASSED{' ' * 30}[{i // 4:3d}%]\n" for i in range(400))
    + "\n=== 400 passed in 3.21s ===\n"
)
EXE = "C:\\Users\\dev\\.local\\bin\\eai.exe"
COMMAND = hook_command_for(EXE)


def _payload(command: str = "pytest -v", stdout: str = PASSING, stderr: str = "") -> dict[str, Any]:
    response = {"stdout": stdout, "stderr": stderr, "interrupted": False, "isImage": False}
    return {"session_id": "s", "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "t",
            "tool_input": {"command": command}, "tool_response": response}


def _ctx(tmp_path: Path, command: str | None = COMMAND) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    eai_path = EXE if command is not None else None
    return Context(tmp_path / "h", {}, Managed(), tmp_path / "proj", eai_path=eai_path)


# Critical 1: stderr is never filtered (Claude Code merges it into stdout anyway).
def test_stderr_is_never_changed(tmp_path: Path) -> None:
    stderr = "".join(f"library warning {i}: something noisy happened\n" for i in range(400))
    out = handle(_payload(stderr=stderr), tmp_path, NOW)
    assert out is not None
    assert out["hookSpecificOutput"]["updatedToolOutput"]["stderr"] == stderr


def test_windows_line_endings_are_handled(tmp_path: Path) -> None:
    out = handle(_payload(stdout=PASSING.replace("\n", "\r\n")), tmp_path, NOW)
    assert out is not None
    assert out["hookSpecificOutput"]["updatedToolOutput"]["stdout"].startswith("Pytest: 400 passed in 3.21s")


# Important 2: combined short flags and other "show me more" pytest options are never trimmed.
@pytest.mark.parametrize(
    "command",
    ["pytest -vs", "pytest -sv tests", "pytest -vls", "pytest -r A", "pytest -V",
     "pytest --log-cli-level=INFO",
     "pytest -o log_cli=true", "pytest --cov=src", "pytest --cov", "pytest --benchmark-only",
     "pytest --hypothesis-show-statistics"],
)
def test_pytest_show_more_flags_never_trim(command: str) -> None:
    assert match_command(command) is None


@pytest.mark.parametrize(
    "command", ["pytest -v", "pytest -k 'slow or s' -v", "pytest -m smoke -q", "pytest -x"]
)
def test_flag_values_are_not_read_as_flags(command: str) -> None:
    assert match_command(command) is not None


# Important 3: coverage tables and skip reasons survive (pytest-cov through addopts has no flag to see).
def test_coverage_block_and_skip_reasons_are_kept() -> None:
    output = """=== test session starts ===
collected 3 items

tests/test_a.py ..s                                                [100%]

---------- coverage: platform linux, python 3.12.0-final-0 -----------
Name          Stmts   Miss  Cover
---------------------------------
src/a.py         10      2    80%
---------------------------------
TOTAL            10      2    80%

=== short test summary info ===
SKIPPED [1] tests/test_a.py:9: needs DB_URL
=== 2 passed, 1 skipped in 0.10s ==="""
    result = filter_pytest(output)
    assert result.startswith("Pytest: 2 passed, 0 failed, 1 skipped in 0.10s")
    assert "TOTAL            10      2    80%" in result
    assert "src/a.py" in result
    assert "SKIPPED [1] tests/test_a.py:9: needs DB_URL" in result


# Important 4: only real build tasks are trimmed.
@pytest.mark.parametrize(
    ("args", "trim"),
    [
        (["build"], True), (["assembleDebug"], True), ([":app:assembleRelease"], True),
        (["clean", "build"], True),
        (["bundleRelease"], True), (["installDebug"], True), (["clean"], True),
        (["buildEnvironment"], False), (["help", "--task", "build"], False), (["myBuildReport"], False),
        (["buildHealth"], False), (["test", "-x", "build"], False),
        (["build", "--warning-mode", "all"], False),
        (["build", "--warning-mode=all"], False), (["build", "--exclude-task", "test"], True),
    ],
)
def test_trimmable_build_tasks(args: list[str], trim: bool) -> None:
    assert is_trimmable_build(args) is trim


# Important 5: off / uninstall remove our handlers by command, even when the group was edited.
def test_off_removes_our_handlers_from_an_edited_group(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "trim")
    data = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    extra = {"type": "command", "command": "my-linter"}
    data["hooks"]["PostToolUse"][0]["hooks"].append(extra)
    ctx.settings_path.write_text(json.dumps(data), encoding="utf-8")
    assert "on: " in next(line for line in status(ctx).lines if line.split()[:1] == ["trim"])
    turn_off(ctx, "trim")
    after = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    assert after["hooks"]["PostToolUse"] == [{"matcher": "Bash", "hooks": [extra]}]


def test_uninstall_restores_exactly_after_trim(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text('{"model": "opus"}', encoding="utf-8")
    turn_on(ctx, "trim")
    uninstall(ctx)
    assert ctx.settings_path.read_text(encoding="utf-8") == '{"model": "opus"}'


# Important 6: saved full outputs do not outlive their promise.
def test_turning_trim_off_deletes_saved_outputs(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "trim")
    log = ctx.state_dir / "trim" / "s" / "t.log"
    log.parent.mkdir(parents=True)
    log.write_text("full output", encoding="utf-8")
    turn_off(ctx, "trim")
    assert not log.exists()


def test_status_and_init_expire_old_logs(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "trim")
    log = ctx.state_dir / "trim" / "s" / "t.log"
    log.parent.mkdir(parents=True)
    log.write_text("full output", encoding="utf-8")
    old = os.path.getmtime(log) - (KEEP_DAYS + 1) * 86400
    os.utime(log, (old, old))
    status(ctx)
    assert not log.exists()


def test_expire_keeps_fresh_empty_folders(tmp_path: Path) -> None:
    fresh = tmp_path / "trim" / "other-session"
    fresh.mkdir(parents=True)
    stale = tmp_path / "trim" / "stale-session"
    stale.mkdir()
    old = time.time() - (KEEP_DAYS + 1) * 86400
    os.utime(stale, (old, old))
    expire_logs(tmp_path, time.time())
    assert fresh.exists()
    assert not stale.exists()


# Important 7: only a permanent install may be wired into Claude Code.
@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("C:\\Users\\dev\\.local\\bin\\eai.exe", True),
        ("/home/dev/.local/bin/eai", True),
        ("C:\\work\\repo\\.venv\\Scripts\\eai.exe", False),
        ("/home/dev/proj/venv/bin/eai", False),
        ("C:\\Users\\dev\\AppData\\Local\\uv\\cache\\archive-v0\\abc\\Scripts\\eai.exe", False),
        ("/home/dev/.cache/uv/archive-v0/abc/bin/eai", False),
        ("/usr/lib/python3/site-packages/bin/eai", False),
    ],
)
def test_persistent_install_detection(path: str, ok: bool) -> None:
    assert is_persistent_install(path) is ok


# Minors.
def test_footer_path_uses_forward_slashes(tmp_path: Path) -> None:
    out = handle(_payload(), tmp_path, NOW)
    assert out is not None
    footer = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"].splitlines()[-1]
    assert "\\" not in footer


def test_counts_survive_bad_rows(tmp_path: Path) -> None:
    counts = tmp_path / "trim" / "counts.jsonl"
    counts.parent.mkdir(parents=True)
    counts.write_text(
        '{"day": "2999-01-01", "before": "x"}\n[1]\n{"day": "2999-01-01", "before": 5, "after": 1}\n'
    )
    assert read_counts(tmp_path, NOW) == (1, 5, 1)


def test_status_reads_counts_from_the_hook_state_dir(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    counts = other / "trim" / "counts.jsonl"
    counts.parent.mkdir(parents=True)
    counts.write_text('{"day": "2999-01-01", "command": "pytest", "before": 9000, "after": 100}\n')
    (tmp_path / "proj").mkdir()
    ctx = Context(tmp_path / "h", {"EAI_STATE_DIR": str(other)}, Managed(), tmp_path / "proj")
    assert any("trim shortened 1 output" in line for line in status(ctx).lines)


def test_every_if_rule_has_a_matcher_prefix_and_back() -> None:
    assert len(IF_RULES) == len(set(IF_RULES))
    for prefix in PREFIXES:
        assert any(prefix.startswith(rule[5:-1].rstrip("*")) for rule in IF_RULES), prefix
    assert "Bash(npm install*)" not in IF_RULES  # covered by Bash(npm i*)


@pytest.mark.parametrize("command", ["FOO=1 pytest -v", ".venv/bin/pytest -v", "npm.cmd install"])
def test_commands_the_hook_is_never_started_for_do_not_match(command: str) -> None:
    assert match_command(command) is None


def test_hook_command_quoting_escapes_shell_characters() -> None:
    assert hook_command_for('C:\\Users\\a"b$c`d\\eai.exe') == '"C:/Users/a\\"b\\$c\\`d/eai.exe" hook trim'


def test_init_does_not_apply_the_same_rules_twice(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "trim")
    data = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    del data["hooks"]
    ctx.settings_path.write_text(json.dumps(data), encoding="utf-8")
    init(ctx)
    groups = json.loads(ctx.settings_path.read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
    assert groups == [hook_group(COMMAND)]

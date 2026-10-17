import json
from pathlib import Path

import pytest

from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.items import ITEM_IDS
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.trim.install import IF_RULES, hook_command_for, hook_group

EXE = "C:\\Users\\dev\\.local\\bin\\eai.exe"
COMMAND = hook_command_for(EXE)


def _ctx(tmp_path: Path, managed: Managed | None = None, command: str | None = COMMAND) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    eai_path = EXE if command is not None else None
    return Context(tmp_path / "h", {}, managed or Managed(), tmp_path / "proj", eai_path=eai_path)


def _settings(ctx: Context) -> dict[str, object]:
    data: dict[str, object] = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    return data


def test_item_list() -> None:
    assert ITEM_IDS == (
        "compact",
        "effort",
        "deny-reads",
        "deny-deps",
        "cache",
        "trim",
        "read-once",
        "concise",
        "minimal",
        "navigate",
        "subagent-model",
    )


def test_hook_group_shape() -> None:
    assert COMMAND == '"C:/Users/dev/.local/bin/eai.exe" hook trim'
    group = hook_group(COMMAND)
    assert group["matcher"] == "Bash"
    assert [h["if"] for h in group["hooks"]] == list(IF_RULES)
    for handler in group["hooks"]:
        assert (handler["type"], handler["command"], handler["timeout"]) == ("command", COMMAND, 10)
    assert "Bash(pytest*)" in IF_RULES and "Bash(./gradlew *)" in IF_RULES


def test_trim_is_opt_in(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    assert "hooks" not in _settings(ctx)
    line = next(line for line in status(ctx).lines if line.split()[:1] == ["trim"])
    assert "opt-in" in line


def test_on_off_round_trip_keeps_other_hooks(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    mine = {"matcher": "Write", "hooks": [{"type": "command", "command": "my-formatter"}]}
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [mine]}}), encoding="utf-8")
    original = ctx.settings_path.read_bytes()
    assert turn_on(ctx, "trim").code == 0
    groups = _settings(ctx)["hooks"]["PostToolUse"]  # type: ignore[index]
    assert groups == [mine, hook_group(COMMAND)]
    turn_off(ctx, "trim")
    assert _settings(ctx)["hooks"]["PostToolUse"] == [mine]  # type: ignore[index]
    turn_on(ctx, "trim")
    uninstall(ctx)
    assert ctx.settings_path.read_bytes() == original


def test_on_trim_needs_a_permanent_install(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, command=None)
    result = turn_on(ctx, "trim")
    assert result.code == 1
    assert "uv tool install ai-usage-kit" in result.lines[0]
    assert "uv tool update-shell" in result.lines[0]  # installed but not on PATH gives the same refusal
    assert not ctx.settings_path.exists()


@pytest.mark.parametrize(
    ("documents", "reason"),
    [
        ([{"allowManagedHooksOnly": True}], "only its own hooks"),
        ([{"disableAllHooks": True}], "hooks are turned off"),
    ],
)
def test_on_trim_refuses_when_policy_blocks_hooks(
    tmp_path: Path, documents: list[dict[str, object]], reason: str
) -> None:
    ctx = _ctx(tmp_path, managed=Managed(documents=documents, sources=["f"]))
    result = turn_on(ctx, "trim")
    assert result.code == 1 and reason in result.lines[0]
    assert not ctx.settings_path.exists()
    line = next(line for line in status(ctx).lines if line.split()[:1] == ["trim"])
    assert reason in line


def test_user_disable_all_hooks_blocks_trim(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text('{"disableAllHooks": true}', encoding="utf-8")
    assert turn_on(ctx, "trim").code == 1


def test_status_shows_trim_counts(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "trim")
    counts = ctx.state_dir / "trim" / "counts.jsonl"
    counts.parent.mkdir(parents=True, exist_ok=True)
    counts.write_text('{"day": "2999-01-01", "command": "pytest", "before": 20000, "after": 400}\n', "utf-8")
    text = "\n".join(status(ctx).lines)
    assert "trim shortened 1 output" in text and "20,000" in text and "400" in text


def test_init_restores_a_hook_removed_by_hand(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "trim")
    data = _settings(ctx)
    del data["hooks"]
    ctx.settings_path.write_text(json.dumps(data), encoding="utf-8")
    init(ctx)
    assert _settings(ctx)["hooks"]["PostToolUse"] == [hook_group(COMMAND)]  # type: ignore[index]

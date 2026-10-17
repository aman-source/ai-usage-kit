import json
from pathlib import Path

from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.items import ITEM_IDS
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.read_once.install import hook_groups
from ai_usage_kit.trim.install import hook_command_for
from ai_usage_kit.trim.install import hook_groups as trim_groups

EXE = "C:\\Users\\dev\\.local\\bin\\eai.exe"
RO = hook_command_for(EXE, "read-once")
TRIM = hook_command_for(EXE, "trim")


def _ctx(tmp_path: Path) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", {}, Managed(), tmp_path / "proj", eai_path=EXE)


def _hooks(ctx: Context) -> dict[str, object]:
    data = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    hooks: dict[str, object] = data.get("hooks", {})
    return hooks


def test_items() -> None:
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


def test_groups() -> None:
    groups = hook_groups(RO)
    assert RO == '"C:/Users/dev/.local/bin/eai.exe" hook read-once'
    assert {e: g["matcher"] for e, g in groups.items()} == {
        "PreToolUse": "Read",
        "PostToolUse": "Read",
        "SessionStart": "compact|clear",
    }
    assert all(g["hooks"] == [{"type": "command", "command": RO, "timeout": 10}] for g in groups.values())


def test_opt_in(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    assert _hooks(ctx) == {}
    assert "opt-in" in next(line for line in status(ctx).lines if line.split()[:1] == ["read-once"])


def test_on_off_alongside_trim_and_user_hooks(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    mine = {"matcher": "Write", "hooks": [{"type": "command", "command": "fmt"}]}
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [mine]}}), encoding="utf-8")
    original = ctx.settings_path.read_bytes()
    assert turn_on(ctx, "trim").code == 0
    assert turn_on(ctx, "read-once").code == 0
    hooks = _hooks(ctx)
    assert hooks["PostToolUse"] == [mine, trim_groups(TRIM)["PostToolUse"], hook_groups(RO)["PostToolUse"]]
    assert hooks["PreToolUse"] == [hook_groups(RO)["PreToolUse"]]
    assert hooks["SessionStart"] == [hook_groups(RO)["SessionStart"]]
    turn_off(ctx, "read-once")
    assert _hooks(ctx) == {"PostToolUse": [mine, trim_groups(TRIM)["PostToolUse"]]}
    turn_on(ctx, "read-once")
    uninstall(ctx)
    assert ctx.settings_path.read_bytes() == original


def test_status_detects_a_removed_event_and_init_restores_it(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "read-once")
    data = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    del data["hooks"]["SessionStart"]
    ctx.settings_path.write_text(json.dumps(data), encoding="utf-8")
    assert "missing" in next(line for line in status(ctx).lines if line.split()[:1] == ["read-once"])
    init(ctx)
    hooks = _hooks(ctx)
    assert hooks["SessionStart"] == [hook_groups(RO)["SessionStart"]]
    assert hooks["PreToolUse"] == [hook_groups(RO)["PreToolUse"]]  # not duplicated


def test_off_deletes_read_once_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "read-once")
    record = ctx.state_dir / "read-once" / "s" / "main.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}", encoding="utf-8")
    turn_off(ctx, "read-once")
    assert not record.parent.exists()


def test_status_shows_read_once_counts(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "read-once")
    counts = ctx.state_dir / "read-once" / "counts.jsonl"
    counts.parent.mkdir(parents=True, exist_ok=True)
    counts.write_text('{"day": "2999-01-01", "before": 4000, "after": 0}\n', encoding="utf-8")
    assert any("read-once skipped 1 repeated read" in line and "4,000" in line for line in status(ctx).lines)


def test_needs_permanent_install(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    ctx = Context(tmp_path / "h", {}, Managed(), tmp_path / "proj", eai_path=None)
    assert turn_on(ctx, "read-once").code == 1

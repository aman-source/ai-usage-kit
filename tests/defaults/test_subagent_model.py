import json
from pathlib import Path

from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.items import version_key
from ai_usage_kit.defaults.managed import Managed

KEY = "CLAUDE_CODE_SUBAGENT_MODEL"


def _ctx(
    tmp_path: Path,
    environ: dict[str, str] | None = None,
    managed: Managed | None = None,
    version: str | None = "2.1.263",
) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(
        tmp_path / "h", environ or {}, managed or Managed(), tmp_path / "proj", claude_code_version=version
    )


def _write(ctx: Context, data: dict[str, object]) -> None:
    ctx.settings_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.settings_path.write_text(json.dumps(data), encoding="utf-8")


def _settings(ctx: Context) -> dict[str, object]:
    data: dict[str, object] = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    return data


def test_on_sets_sonnet_and_off_removes_created_env(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"model": "opus"})
    result = turn_on(ctx, "subagent-model")
    assert result.code == 0
    assert _settings(ctx)["env"] == {KEY: "sonnet"}
    assert "Sonnet" in result.lines[0]
    turn_off(ctx, "subagent-model")
    assert _settings(ctx) == {"model": "opus"}


def test_existing_env_block_is_kept(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"env": {"FOO": "1"}})
    turn_on(ctx, "subagent-model")
    assert _settings(ctx)["env"] == {"FOO": "1", KEY: "sonnet"}
    turn_off(ctx, "subagent-model")
    assert _settings(ctx)["env"] == {"FOO": "1"}


def test_existing_empty_env_block_is_kept(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"env": {}})
    turn_on(ctx, "subagent-model")
    turn_off(ctx, "subagent-model")
    assert _settings(ctx) == {"env": {}}


def test_user_value_in_settings_is_left_alone(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"env": {KEY: "haiku"}})
    result = turn_on(ctx, "subagent-model")
    assert "skipped" in result.lines[0]
    assert _settings(ctx)["env"] == {KEY: "haiku"}


def test_process_environment_counts_as_set(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, environ={KEY: "opus"})
    _write(ctx, {})
    result = turn_on(ctx, "subagent-model")
    assert result.code == 0
    assert "your environment" in result.lines[0]
    assert _settings(ctx) == {}


def test_empty_process_variable_is_not_set(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, environ={KEY: "  "})
    _write(ctx, {})
    turn_on(ctx, "subagent-model")
    assert _settings(ctx)["env"] == {KEY: "sonnet"}


def test_organization_value_wins(tmp_path: Path) -> None:
    managed = Managed(documents=[{"env": {KEY: "haiku"}}], sources=["managed-settings.json"])
    ctx = _ctx(tmp_path, environ={KEY: "opus"}, managed=managed)
    _write(ctx, {})
    result = turn_on(ctx, "subagent-model")
    assert "organization" in result.lines[0]
    assert _settings(ctx) == {}


def test_old_claude_code_is_refused(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, version="2.1.9")
    _write(ctx, {})
    result = turn_on(ctx, "subagent-model")
    assert result.code == 1
    assert "2.1.251" in result.lines[0] and "2.1.9" in result.lines[0]
    assert _settings(ctx) == {}


def test_unknown_version_is_allowed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, version=None)
    _write(ctx, {})
    assert turn_on(ctx, "subagent-model").code == 0


def test_user_changed_value_is_kept_on_off(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {})
    turn_on(ctx, "subagent-model")
    _write(ctx, {"env": {KEY: "haiku"}})
    result = turn_off(ctx, "subagent-model")
    assert "kept your own" in result.lines[0]
    assert _settings(ctx)["env"] == {KEY: "haiku"}


def test_not_applied_by_init_and_shown_in_status(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {})
    init(ctx)
    assert "env" not in _settings(ctx)
    assert any("subagent-model" in line and "opt-in" in line for line in status(ctx).lines)


def test_init_restores_missing_value_and_uninstall_is_exact(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"model": "opus"})
    original = ctx.settings_path.read_bytes()
    turn_on(ctx, "subagent-model")
    _write(ctx, {"model": "opus"})  # someone removed it
    init(ctx)
    assert _settings(ctx)["env"] == {KEY: "sonnet"}
    uninstall(ctx)
    assert ctx.settings_path.read_bytes() == original


def test_restore_keeps_created_parent_so_off_prunes_env(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {})
    turn_on(ctx, "subagent-model")
    _write(ctx, {"env": {}})  # value removed, empty block left behind
    init(ctx)  # restores the value into the existing block
    turn_off(ctx, "subagent-model")
    assert "env" not in _settings(ctx)


def test_version_key_is_numeric() -> None:
    assert version_key("2.1.9") < version_key("2.1.251")
    assert version_key("2.1.251") <= version_key("2.1.263")


def test_status_says_set_in_environment(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, environ={KEY: "opus"})
    _write(ctx, {})
    line = next(line for line in status(ctx).lines if "subagent-model" in line)
    assert "your environment" in line

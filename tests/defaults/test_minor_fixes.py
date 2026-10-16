"""Tests for the minor findings from the defaults review."""

import json
from pathlib import Path

from ai_usage_kit.defaults.commands import Context, init, status, uninstall
from ai_usage_kit.defaults.items import api_billing
from ai_usage_kit.defaults.managed import Managed, read_managed


def _ctx(tmp_path: Path, environ: dict[str, str] | None = None) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", environ or {}, Managed(), tmp_path / "proj")


def _write(ctx: Context, data: bytes) -> None:
    ctx.settings_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.settings_path.write_bytes(data)


def _item_line(lines: list[str], item_id: str) -> str:
    return next(line for line in lines if line.split()[:1] == [item_id])


# 1: status checks settings.json, not only eai's own record; init repairs what was removed.
def test_status_and_init_notice_a_value_removed_by_hand(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    data = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    del data["autoCompactWindow"]
    _write(ctx, json.dumps(data).encode())
    assert "missing from settings.json" in _item_line(status(ctx).lines, "compact")
    init(ctx)
    assert json.loads(ctx.settings_path.read_text(encoding="utf-8"))["autoCompactWindow"] == 200000
    assert _item_line(status(ctx).lines, "compact").split()[1] == "on:"


def test_status_notes_a_per_model_override(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    data = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    data["modelSettings"] = {"claude-opus-5-5": {"autoCompactWindow": 500000}}
    _write(ctx, json.dumps(data).encode())
    line = _item_line(status(ctx).lines, "compact")
    assert "per-model setting" in line and "claude-opus-5-5" in line


# 2: an empty permissions object survives install and uninstall; a deleted file is reported plainly.
def test_empty_permissions_object_round_trip(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    original = b'{"permissions": {}}\n'
    _write(ctx, original)
    init(ctx)
    result = uninstall(ctx)
    assert ctx.settings_path.read_bytes() == original
    assert "Restored your original settings.json exactly." in result.lines


def test_uninstall_after_settings_file_was_deleted(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, b'{"model": "opus"}')
    init(ctx)
    ctx.settings_path.unlink()
    result = uninstall(ctx)
    assert not ctx.settings_path.exists()
    assert any("was deleted after install" in line for line in result.lines)
    assert not any("later edits" in line for line in result.lines)


# 3: dry runs say what would happen.
def test_dry_run_lines_say_would(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert _item_line(init(ctx, dry_run=True).lines, "compact").split()[1:3] == ["would", "turn"]
    init(ctx)
    assert _item_line(uninstall(ctx, dry_run=True).lines, "compact").split()[1:3] == ["would", "remove"]


# 4: Claude Code ignores the user-writable HKCU policy when an admin source is present.
def test_hkcu_is_ignored_when_an_admin_source_exists(tmp_path: Path) -> None:
    hkcu = [("HKCU\\SOFTWARE\\Policies\\ClaudeCode", '{"effortLevel": "low"}')]
    assert read_managed(tmp_path / "absent", lambda: hkcu).sets("effortLevel")
    (tmp_path / "managed-settings.json").write_text("{}", encoding="utf-8")
    managed = read_managed(tmp_path, lambda: hkcu)
    assert not managed.sets("effortLevel")
    hklm = [("HKLM\\SOFTWARE\\Policies\\ClaudeCode", "{}"), *hkcu]
    assert not read_managed(tmp_path / "absent", lambda: hklm).sets("effortLevel")


# 5: an LLM gateway is API-style billing too.
def test_gateway_counts_as_api_billing() -> None:
    assert api_billing({"ANTHROPIC_BASE_URL": "https://llm-gateway.example"}, {}) == "ANTHROPIC_BASE_URL"
    assert api_billing({"ANTHROPIC_AUTH_TOKEN": "t"}, {}) == "ANTHROPIC_AUTH_TOKEN"


# 6: CLAUDE_CONFIG_DIR moves the user settings file.
def test_claude_config_dir_is_respected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")})
    assert ctx.settings_path == tmp_path / "cfg" / "settings.json"
    init(ctx)
    assert (tmp_path / "cfg" / "settings.json").exists()
    assert not (tmp_path / "h" / ".claude" / "settings.json").exists()

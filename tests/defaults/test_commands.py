import json
from pathlib import Path

import pytest

from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.defaults.settings import SettingsError

ORIGINAL = '{\n    "model": "opus",\n    "note": "caf\u00e9"\n}\n'


def _ctx(tmp_path: Path, environ: dict[str, str] | None = None, managed: Managed | None = None) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", environ or {}, managed or Managed(), tmp_path / "proj")


def _write(ctx: Context, text: str) -> None:
    ctx.settings_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.settings_path.write_text(text, encoding="utf-8")


def _settings(ctx: Context) -> dict[str, object]:
    data: dict[str, object] = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    return data


def _files(root: Path) -> list[tuple[str, bytes]]:
    return sorted((str(p.relative_to(root)), p.read_bytes()) for p in root.rglob("*") if p.is_file())


def test_init_applies_default_items_and_backs_up(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, ORIGINAL)
    result = init(ctx)
    data = _settings(ctx)
    assert data["model"] == "opus"
    assert data["autoCompactWindow"] == 200000
    assert data["effortLevel"] == "medium"
    assert "Read(dist/**)" in data["permissions"]["deny"]  # type: ignore[index]
    assert "promptCacheTtl" not in data
    assert ctx.backup_path.read_text(encoding="utf-8") == ORIGINAL
    assert result.code == 0
    assert "Undo everything with: eai uninstall" in result.lines


def test_init_dry_run_writes_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, ORIGINAL)
    before = _files(tmp_path)
    result = init(ctx, dry_run=True)
    assert _files(tmp_path) == before
    assert result.lines[0] == "Dry run: nothing was written."


def test_init_twice_is_idempotent(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    first = _settings(ctx)
    second_run = init(ctx)
    assert _settings(ctx) == first
    assert any(line.split()[:2] == ["compact", "on"] for line in second_run.lines)


def test_uninstall_restores_original_bytes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, ORIGINAL)
    init(ctx)
    result = uninstall(ctx)
    assert ctx.settings_path.read_text(encoding="utf-8") == ORIGINAL
    assert not ctx.state_dir.exists()
    assert "Restored your original settings.json exactly." in result.lines


def test_uninstall_removes_a_file_we_created(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    assert ctx.settings_path.exists()
    uninstall(ctx)
    assert not ctx.settings_path.exists()


def test_uninstall_keeps_edits_made_after_init(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, ORIGINAL)
    init(ctx)
    data = _settings(ctx)
    data["effortLevel"] = "high"
    data["theme"] = "dark"
    _write(ctx, json.dumps(data))
    result = uninstall(ctx)
    after = _settings(ctx)
    assert after["effortLevel"] == "high"
    assert after["theme"] == "dark"
    assert "autoCompactWindow" not in after
    assert "permissions" not in after
    kept = ctx.settings_path.with_name("settings.json.ai-usage-kit-backup")
    assert kept.read_text(encoding="utf-8") == ORIGINAL
    assert any("you changed effortLevel" in line for line in result.lines)
    assert any(str(kept) in line for line in result.lines)


def test_off_stays_off_and_on_turns_back_on(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    turn_off(ctx, "effort")
    assert "effortLevel" not in _settings(ctx)
    init(ctx)
    assert "effortLevel" not in _settings(ctx)
    turn_on(ctx, "effort")
    assert _settings(ctx)["effortLevel"] == "medium"


def test_cache_needs_api_billing_and_never_prints_the_key(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    refused = turn_on(ctx, "cache")
    assert refused.code == 1
    assert not ctx.settings_path.exists()
    api = _ctx(tmp_path, environ={"ANTHROPIC_API_KEY": "sk-secret-123"})
    on = turn_on(api, "cache")
    assert on.code == 0
    assert _settings(api)["promptCacheTtl"] == "1h"
    shown = "\n".join(on.lines + status(api).lines)
    assert "sk-secret-123" not in shown
    other = _ctx(tmp_path / "x", {"ANTHROPIC_API_KEY": "sk-secret-123"})
    assert "ANTHROPIC_API_KEY" in "\n".join(status(other).lines)


def test_status_shows_each_item(tmp_path: Path) -> None:
    source = "C:/Program Files/ClaudeCode/managed-settings.json"
    org = Managed(documents=[{"effortLevel": "low"}], sources=[source])
    ctx = _ctx(tmp_path, managed=org)
    lines = status(ctx).lines
    text = "\n".join(lines)
    assert any(line.split()[:1] == ["effort"] and "set by your organization" in line for line in lines)
    assert any(line.split()[:1] == ["cache"] and "not applicable" in line for line in lines)
    assert "Organization settings found: C:/Program Files/ClaudeCode/managed-settings.json" in text
    assert "/status" in text


def test_organization_value_is_not_overridden(tmp_path: Path) -> None:
    org = Managed(documents=[{"effortLevel": "low"}], sources=["f"])
    ctx = _ctx(tmp_path, managed=org)
    init(ctx)
    assert "effortLevel" not in _settings(ctx)


@pytest.mark.parametrize("text", ["{bad", '{"permissions": "x"}'])
def test_unusable_settings_change_nothing(tmp_path: Path, text: str) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, text)
    with pytest.raises(SettingsError):
        init(ctx)
    assert ctx.settings_path.read_text(encoding="utf-8") == text
    assert not ctx.state_dir.exists()


def test_init_warns_about_big_claude_md(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.cwd / "CLAUDE.md").write_text("line\n" * 250, encoding="utf-8")
    assert any(line.startswith("warning:") and "250 lines" in line for line in init(ctx, dry_run=True).lines)


def test_uninstall_without_install(tmp_path: Path) -> None:
    assert uninstall(_ctx(tmp_path)).lines == [
        "Nothing to uninstall: ai-usage-kit has not changed your settings."
    ]

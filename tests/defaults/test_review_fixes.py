"""Tests for the final-review findings on the defaults branch."""

import json
from pathlib import Path
from typing import Any

import pytest

from ai_usage_kit import cli
from ai_usage_kit.defaults import commands
from ai_usage_kit.defaults.commands import Context, init, status, uninstall
from ai_usage_kit.defaults.items import ITEM_IDS, apply_item, get_item
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.defaults.settings import SettingsError, load_settings


def _ctx(tmp_path: Path) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", {}, Managed(), tmp_path / "proj")


def _write(ctx: Context, data: bytes) -> None:
    ctx.settings_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.settings_path.write_bytes(data)


# Important 1: backups may hold secrets, so they are private to the user.
def test_backups_are_made_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: dict[str, int] = {}
    monkeypatch.setattr("os.chmod", lambda path, mode: modes.__setitem__(Path(path).name, mode))
    ctx = _ctx(tmp_path)
    _write(ctx, b'{"env": {"ANTHROPIC_API_KEY": "sk-secret"}}')
    init(ctx)
    assert modes.get("settings.json") == 0o600
    assert modes.get(".ai-usage-kit") == 0o700


# Important 2: file-system errors are one line, not a traceback.
def test_filesystem_error_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def locked(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied", "settings.json")

    monkeypatch.setattr(commands, "init", locked)
    monkeypatch.setattr(cli, "system_managed", lambda: Managed())
    assert cli.main(["init"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and err.count("\n") == 1


def test_non_utf8_settings_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b'{"a": "caf\xe9"}')
    with pytest.raises(SettingsError, match="UTF-8"):
        load_settings(path)


# Important 3: Windows editors often write a UTF-8 BOM.
def test_utf8_bom_is_accepted_and_restored(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    original = b'\xef\xbb\xbf{"model": "opus"}'
    _write(ctx, original)
    assert load_settings(ctx.settings_path) == {"model": "opus"}
    init(ctx)
    uninstall(ctx)
    assert ctx.settings_path.read_bytes() == original


# Important 4: a write by Claude Code while eai runs is not lost.
def test_concurrent_change_stops_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, b'{"model": "opus"}')
    real = apply_item

    def racing(*args: Any, **kwargs: Any) -> Any:
        ctx.settings_path.write_bytes(b'{"model": "opus", "fastMode": true}')
        return real(*args, **kwargs)

    monkeypatch.setattr(commands, "apply_item", racing)
    with pytest.raises(SettingsError, match="changed while"):
        init(ctx)
    assert ctx.settings_path.read_bytes() == b'{"model": "opus", "fastMode": true}'


# Important 5: a damaged or older state file never blocks uninstall with a traceback.
DAMAGED = ["[]", '{"applied": [], "off": []}', '{"applied": {"compact": {}}, "off": []}']


@pytest.mark.parametrize("state", DAMAGED)
def test_damaged_state_is_a_clear_error(tmp_path: Path, state: str) -> None:
    ctx = _ctx(tmp_path)
    ctx.state_dir.mkdir(parents=True)
    ctx.state_path.write_text(state, encoding="utf-8")
    with pytest.raises(SettingsError, match="damaged"):
        status(ctx)
    with pytest.raises(SettingsError, match="damaged"):
        uninstall(ctx)


def test_uninstall_undoes_items_from_an_older_version(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, b'{"oldKey": 1, "model": "opus"}')
    ctx.state_dir.mkdir(parents=True)
    state = {"version": 1, "applied": {"old-item": {"key": "oldKey", "value": 1}}, "off": []}
    ctx.state_path.write_text(json.dumps(state), encoding="utf-8")
    result = uninstall(ctx)
    assert json.loads(ctx.settings_path.read_text(encoding="utf-8")) == {"model": "opus"}
    assert any("old-item" in line and "removed" in line for line in result.lines)


# Important 6: defaults must not block reading dependencies or lock files.
def test_default_deny_rules_only_cover_generated_output() -> None:
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
    default = get_item("deny-reads").value
    moved = ("Read(node_modules/**)", "Read(.venv/**)", "Read(vendor/**)", "Read(build/**)", "Read(uv.lock)")
    for rule in moved:
        assert rule not in default
        assert rule in get_item("deny-deps").value
    assert "Read(dist/**)" in default
    assert get_item("deny-deps").default_on is False


def test_deny_deps_is_opt_in_without_billing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    assert "Read(node_modules/**)" not in ctx.settings_path.read_text(encoding="utf-8")
    assert commands.turn_on(ctx, "deny-deps").code == 0
    assert "Read(node_modules/**)" in ctx.settings_path.read_text(encoding="utf-8")


def test_init_says_how_to_unblock_reads(tmp_path: Path) -> None:
    assert any("eai off deny-reads" in line for line in init(_ctx(tmp_path), dry_run=True).lines)


# Promoted minor: a deny value that is not a list is never overwritten.
def test_non_list_deny_is_an_error() -> None:
    with pytest.raises(SettingsError, match="deny"):
        apply_item({"permissions": {"deny": "Read(x)"}}, get_item("deny-reads"), Managed())


# Promoted minor: state is saved before settings, so a failed write never leaves untracked keys.
def test_state_is_saved_before_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)

    def fail(*args: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(commands, "_write_settings", fail)
    with pytest.raises(OSError):
        init(ctx)
    assert "compact" in json.loads(ctx.state_path.read_text(encoding="utf-8"))["applied"]

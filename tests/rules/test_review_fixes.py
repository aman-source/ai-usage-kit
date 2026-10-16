"""Tests for the final-review findings on the concise/minimal branch."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ai_usage_kit.defaults import commands
from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.defaults.settings import SettingsError
from ai_usage_kit.rules.texts import CONCISE, MINIMAL


def _flat(text: str) -> str:
    return " ".join(text.lower().split())


# Important 1-4: concise keeps the reference's precedence, warning, comment and clarity rules.
@pytest.mark.parametrize(
    "phrase",
    [
        "come first",
        "follow them in full",
        "subagent",
        "to warn, to clarify, or to mark a new step",
        "as much detail as the answer needs",
        "existing comments in files you edit",
        'never drop "not", "no", "never", "only" or "except"',
        "choose clearer",
        "confirm first",
        "messages to other people",
        "memory files",
        "user's language",
        "if it is new",
    ],
)
def test_concise_keeps_reference_safety_rules(phrase: str) -> None:
    assert phrase in _flat(CONCISE)


# Important 5-6: minimal keeps the reference's safety rules, with unambiguous wording.
@pytest.mark.parametrize(
    "phrase",
    [
        "data it could delete or leak",
        "callers it could break",
        "right for edge cases",
        "gets at least one small test or check",
        "security measures (checks, encryption, secret handling, redaction)",
        "silent failures",
        "anything the user asked for",
        "known limit:",
        "in addition to any other closing line",
    ],
)
def test_minimal_keeps_reference_safety_rules(phrase: str) -> None:
    assert phrase in _flat(MINIMAL)


def test_minimal_never_protects_failure_hiding() -> None:
    assert "hides failures" not in _flat(MINIMAL)


def _ctx(tmp_path: Path) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", {}, Managed(), tmp_path / "proj")


def _rule(ctx: Context, name: str) -> Path:
    return ctx.settings_path.parent / "rules" / f"eai-{name}.md"


def _line(ctx: Context, item: str, lines: list[str] | None = None) -> str:
    return next(line for line in (lines or status(ctx).lines) if line.split()[:1] == [item])


# Important 7: init restore saves the record before writing the file.
def test_init_restore_commits_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "minimal")
    _rule(ctx, "minimal").unlink()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise SettingsError("changed while running")

    monkeypatch.setattr(commands, "_check_unchanged", fail)
    with pytest.raises(SettingsError):
        init(ctx)
    assert not _rule(ctx, "minimal").exists()  # nothing written when the commit failed


# Minors.
def test_crlf_resaved_file_is_kept(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "concise")
    _rule(ctx, "concise").write_bytes(CONCISE.replace("\n", "\r\n").encode("utf-8"))
    turn_off(ctx, "concise")
    assert _rule(ctx, "concise").exists()


def test_orphaned_file_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "concise")
    real_unlink = Path.unlink

    def locked(self: Path, missing_ok: bool = False) -> None:
        if self.name == "eai-concise.md":
            raise PermissionError(13, "locked")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(PermissionError):
        turn_off(ctx, "concise")
    monkeypatch.undo()
    assert "still there" in _line(ctx, "concise")


def test_init_reports_an_edited_rule(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "concise")
    _rule(ctx, "concise").write_text("mine\n", encoding="utf-8")
    assert "you changed" in _line(ctx, "concise", init(ctx).lines)


def test_directory_at_rule_path_is_refused_cleanly(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _rule(ctx, "concise").mkdir(parents=True)
    result = turn_on(ctx, "concise")
    assert result.code == 1 and "already exists" in result.lines[0]


def test_rule_only_use_makes_no_settings_backup(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text('{"model": "opus"}', encoding="utf-8")
    turn_on(ctx, "concise")
    assert not ctx.backup_path.exists()
    ctx.settings_path.write_text('{"model": "sonnet"}', encoding="utf-8")  # the user's own later edit
    result = uninstall(ctx)
    assert ctx.settings_path.read_text(encoding="utf-8") == '{"model": "sonnet"}'
    assert not ctx.settings_path.with_name("settings.json.ai-usage-kit-backup").exists()
    assert not any("later edits" in line for line in result.lines)


def test_uninstall_removes_the_rules_folder_it_emptied(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "concise")
    uninstall(ctx)
    assert not (ctx.settings_path.parent / "rules").exists()


def test_init_upgrades_an_unedited_file_to_the_current_text(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "concise")
    old = "# Concise replies\nolder text\n"
    _rule(ctx, "concise").write_text(old, encoding="utf-8", newline="")
    data = json.loads(ctx.state_path.read_text(encoding="utf-8"))
    data["applied"]["concise"]["rule_sha256"] = hashlib.sha256(old.encode("utf-8")).hexdigest()
    ctx.state_path.write_text(json.dumps(data), encoding="utf-8")
    assert "updated" in _line(ctx, "concise", init(ctx).lines)
    assert _rule(ctx, "concise").read_text(encoding="utf-8") == CONCISE
    assert _line(ctx, "concise").split()[1] == "on:"

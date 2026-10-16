import hashlib
import json
from pathlib import Path

from ai_usage_kit.defaults.commands import Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.items import ITEM_IDS
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.rules.texts import CONCISE, MINIMAL


def _ctx(tmp_path: Path, environ: dict[str, str] | None = None) -> Context:
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", environ or {}, Managed(), tmp_path / "proj")


def _rule(ctx: Context, name: str) -> Path:
    return ctx.settings_path.parent / "rules" / f"eai-{name}.md"


def _line(ctx: Context, item: str) -> str:
    return next(line for line in status(ctx).lines if line.split()[:1] == [item])


def test_items() -> None:
    assert ITEM_IDS[-2:] == ("concise", "minimal")


def test_opt_in(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    init(ctx)
    assert not (ctx.settings_path.parent / "rules").exists()
    assert "opt-in" in _line(ctx, "concise") and "opt-in" in _line(ctx, "minimal")


def test_on_writes_exact_text_and_off_removes_it(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert turn_on(ctx, "concise").code == 0
    assert turn_on(ctx, "minimal").code == 0
    assert _rule(ctx, "concise").read_text(encoding="utf-8") == CONCISE
    assert _rule(ctx, "minimal").read_text(encoding="utf-8") == MINIMAL
    assert _line(ctx, "concise").split()[1] == "on:"
    turn_off(ctx, "concise")
    assert not _rule(ctx, "concise").exists() and _rule(ctx, "minimal").exists()
    assert "off" in _line(ctx, "concise")


def test_settings_bytes_never_change(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.settings_path.parent.mkdir(parents=True)
    ctx.settings_path.write_text('{\n    "model": "opus"\n}\n', encoding="utf-8")
    before = ctx.settings_path.read_bytes()
    turn_on(ctx, "concise")
    turn_off(ctx, "concise")
    turn_on(ctx, "minimal")
    assert ctx.settings_path.read_bytes() == before
    uninstall(ctx)
    assert ctx.settings_path.read_bytes() == before
    assert not _rule(ctx, "minimal").exists()


def test_foreign_file_is_never_overwritten(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    path = _rule(ctx, "concise")
    path.parent.mkdir(parents=True)
    path.write_text("# my own rules\n", encoding="utf-8")
    result = turn_on(ctx, "concise")
    assert result.code == 1 and "already exists" in result.lines[0]
    assert path.read_text(encoding="utf-8") == "# my own rules\n"
    assert not ctx.state_dir.exists()


def test_identical_existing_file_is_adopted(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    path = _rule(ctx, "concise")
    path.parent.mkdir(parents=True)
    path.write_text(CONCISE, encoding="utf-8", newline="")
    assert turn_on(ctx, "concise").code == 0
    assert path.read_text(encoding="utf-8") == CONCISE


def test_edited_file_is_kept_on_off_and_uninstall(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "concise")
    path = _rule(ctx, "concise")
    path.write_text(CONCISE + "- my extra rule\n", encoding="utf-8")
    assert "you changed" in _line(ctx, "concise")
    result = turn_off(ctx, "concise")
    assert path.exists() and any("kept" in line for line in result.lines)
    turn_on(ctx, "minimal")
    path_min = _rule(ctx, "minimal")
    path_min.write_text("edited\n", encoding="utf-8")
    result = uninstall(ctx)
    assert path_min.exists() and any("minimal" in line and "you changed" in line for line in result.lines)


def test_init_restores_a_deleted_rule(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "minimal")
    _rule(ctx, "minimal").unlink()
    assert "missing" in _line(ctx, "minimal")
    init(ctx)
    assert _rule(ctx, "minimal").read_text(encoding="utf-8") == MINIMAL


def test_dry_runs_write_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert turn_on(ctx, "concise", dry_run=True).lines[0] == "Dry run: nothing was written."
    assert not ctx.home.exists()
    turn_on(ctx, "concise")
    turn_off(ctx, "concise", dry_run=True)
    uninstall(ctx, dry_run=True)
    assert _rule(ctx, "concise").exists()


def test_claude_config_dir_is_respected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")})
    turn_on(ctx, "concise")
    assert (tmp_path / "cfg" / "rules" / "eai-concise.md").exists()


def test_record_stores_hash_only(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    turn_on(ctx, "concise")
    record = json.loads(ctx.state_path.read_text(encoding="utf-8"))["applied"]["concise"]
    assert record == {"key": "rules/eai-concise.md",
                      "rule_sha256": hashlib.sha256(CONCISE.encode("utf-8")).hexdigest()}

import io
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_usage_kit import cli
from ai_usage_kit.cli import main, parse_since
from ai_usage_kit.defaults.managed import Managed
from tests.analyze.fixture_tree import build_fixture_tree
from tests.analyze.records import assistant, write_jsonl


@pytest.fixture
def fixture_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Sample logs under a temp home; Path.home() points there so tests never touch the real home dir."""
    root, home = build_fixture_tree(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: home)
    return root, home


def test_no_command_is_an_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_analyze_end_to_end(
    tmp_path: Path, fixture_home: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, home = fixture_home
    before = sorted(p.relative_to(home) for p in home.rglob("*"))
    out, export = tmp_path / "r.md", tmp_path / "e.json"
    assert main(["analyze", "--out", str(out), "--export", str(export)]) == 0
    printed = capsys.readouterr().out
    assert "2 sessions in 2 projects" in printed
    assert f"Report written to {out}" in printed
    assert out.read_text(encoding="utf-8").startswith("# Token usage report")
    assert json.loads(export.read_text(encoding="utf-8"))["sessions"] == 2
    assert sorted(p.relative_to(home) for p in home.rglob("*")) == before


def test_no_sessions_is_not_an_error(
    fixture_home: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = fixture_home
    assert main(["analyze", "--root", str(root / "none")]) == 0
    assert "No Claude Code sessions found under" in capsys.readouterr().out


def test_threshold_flag_changes_findings(
    fixture_home: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["analyze", "--large-output-chars", "100000"]) == 0
    assert re.search(r"\| large-tool-output +\| +0 \|", capsys.readouterr().out)


def test_parse_since() -> None:
    assert parse_since("2026-09-01") == datetime(2026, 9, 1, tzinfo=UTC)
    assert abs(parse_since("30d") - (datetime.now(UTC) - timedelta(days=30))) < timedelta(seconds=5)
    with pytest.raises(SystemExit) as exc:
        main(["analyze", "--since", "soon"])
    assert exc.value.code == 2


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_top_must_be_positive(value: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["analyze", "--top", value])
    assert exc.value.code == 2


@pytest.mark.parametrize("flag", ["--out", "--export"])
def test_unwritable_output_is_a_one_line_error(
    tmp_path: Path, fixture_home: tuple[Path, Path], capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    target = tmp_path / "missing-dir" / "file"
    assert main(["analyze", flag, str(target)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"error: cannot write {target}:")
    assert "Traceback" not in err
    assert not target.parent.exists()


def test_non_ascii_project_on_cp1252_console(
    fixture_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = fixture_home
    write_jsonl(root / "C--work-日本" / "s.jsonl", [assistant("m1", usage=(1, 1, 1, 99_999))])
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    assert main(["analyze"]) == 0
    stream.flush()
    assert b"C--work-??" in buffer.getvalue()


@pytest.fixture
def no_org(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No organization settings and no billing env vars; cwd is an empty temp project."""
    monkeypatch.setattr(cli, "system_managed", lambda: Managed())
    for name in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_ANTHROPIC_AWS",
        "CLAUDE_CODE_USE_MANTLE",
    ):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return Path.home()


def test_init_status_uninstall_round_trip(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = no_org / ".claude" / "settings.json"
    assert main(["init"]) == 0
    assert json.loads(settings.read_text(encoding="utf-8"))["autoCompactWindow"] == 200000
    assert main(["status"]) == 0
    assert "compact" in capsys.readouterr().out
    assert main(["uninstall"]) == 0
    assert not settings.exists()
    assert not (no_org / ".ai-usage-kit").exists()


def test_init_dry_run_creates_nothing(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--dry-run"]) == 0
    assert list(no_org.iterdir()) == []
    assert capsys.readouterr().out.startswith("Dry run: nothing was written.")


def test_unusable_settings_is_a_one_line_error(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = no_org / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{bad", encoding="utf-8")
    assert main(["init"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and err.count("\n") == 1
    assert settings.read_text(encoding="utf-8") == "{bad"


def test_on_cache_without_api_billing_fails(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["on", "cache"]) == 1
    assert "none was detected" in capsys.readouterr().err


def test_on_subagent_model_refuses_old_claude_code(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = no_org / ".claude" / "projects" / "p" / "s.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"type": "user", "version": "2.1.9", "message": {"content": "x"}}) + "\n", encoding="utf-8"
    )
    assert main(["on", "subagent-model"]) == 1
    assert "found 2.1.9" in capsys.readouterr().err
    assert not (no_org / ".claude" / "settings.json").exists()


def test_on_subagent_model_with_recent_claude_code(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = no_org / ".claude" / "projects" / "p" / "s.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"type": "user", "version": "2.1.263", "message": {"content": "x"}}) + "\n",
        encoding="utf-8",
    )
    assert main(["on", "subagent-model"]) == 0
    data = json.loads((no_org / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert data["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "sonnet"


def test_unknown_item_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["off", "everything"])
    assert exc.value.code == 2


def test_claude_config_dir_is_where_logs_are_read(
    no_org: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "custom-claude"
    log = config / "projects" / "p" / "s.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"type": "user", "version": "2.1.9", "message": {"content": "x"}}) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    assert main(["on", "subagent-model"]) == 1
    assert "found 2.1.9" in capsys.readouterr().err


def test_on_recommended_turns_on_the_safe_set(
    no_org: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_eai_path", lambda: "C:/tools/eai.exe")
    assert main(["on", "recommended"]) == 0
    out = capsys.readouterr().out
    for item in ("trim", "read-once", "concise", "minimal", "subagent-model"):
        assert re.search(rf"^  {item} +on: ", out, re.M)
    assert "navigate" in out and "deny-deps" in out and "cache" in out  # named as left off
    data = json.loads((no_org / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert data["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "sonnet"
    assert "navigate" not in json.dumps(data)
    assert (no_org / ".claude" / "rules" / "eai-concise.md").exists()
    assert main(["off", "recommended"]) == 0
    assert not (no_org / ".claude" / "rules" / "eai-concise.md").exists()
    assert "CLAUDE_CODE_SUBAGENT_MODEL" not in (no_org / ".claude" / "settings.json").read_text(
        encoding="utf-8"
    )


def test_on_recommended_does_not_call_an_item_given_too_left_off(
    no_org: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_eai_path", lambda: "C:/tools/eai.exe")
    assert main(["on", "recommended", "navigate"]) == 0
    left_off = [line for line in capsys.readouterr().out.splitlines() if line.startswith("left off")]
    assert left_off == ["left off (turn on one by one if they fit): deny-deps, cache"]


def test_on_several_items_once_each(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["on", "concise", "minimal", "concise", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("Dry run: nothing was written.") == 1
    assert len(re.findall(r"^  concise ", out, re.M)) == 1 and re.search(
        r"^  minimal +would turn on", out, re.M
    )
    assert not (no_org / ".claude").exists()
    assert main(["on", "concise", "minimal"]) == 0
    assert (no_org / ".claude" / "rules" / "eai-minimal.md").exists()


def test_one_refused_item_does_not_stop_the_others(no_org: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["on", "cache", "concise"]) == 1
    captured = capsys.readouterr()
    assert "none was detected" in captured.err
    assert re.search(r"^  concise +on: ", captured.out, re.M)
    assert (no_org / ".claude" / "rules" / "eai-concise.md").exists()


def test_dry_run_line_and_error_labels_with_several_items(
    no_org: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["on", "cache", "concise", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert captured.out.startswith("Dry run: nothing was written.")
    assert captured.err.startswith("cache: error: ")
    assert not (no_org / ".claude").exists()

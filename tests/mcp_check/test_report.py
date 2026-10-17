import json
from pathlib import Path

import pytest

from ai_usage_kit import cli
from ai_usage_kit.cli import main
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.mcp_check.config import Server
from ai_usage_kit.mcp_check.report import ALL_SCHEMAS, render, tool_name_server, usage_by_server
from ai_usage_kit.mcp_check.toolsearch import ToolSearch
from tests.analyze.records import assistant, write_jsonl

ON = ToolSearch("on", "default")
OFF = ToolSearch("off", "requests go through a custom gateway URL (ANTHROPIC_BASE_URL)")


def _server(name: str, scope: str = "user", disabled: bool = False) -> Server:
    return Server(name, scope, "stdio", False, disabled)


def test_tool_name_server() -> None:
    assert tool_name_server("my.server") == "my_server"
    assert tool_name_server("notes-vault") == "notes-vault"
    assert tool_name_server("a b/c") == "a_b_c"


def test_usage_by_server() -> None:
    assert usage_by_server({"mcp:a": 2, "Read": 5, "mcp:b": 1}) == {"a": 2, "b": 1}


def test_used_server_has_no_advice() -> None:
    text = "\n".join(render(ON, [_server("used")], {"used": 3}, {"used": 3}, [], 30))
    assert "3 here" in text and "unused" not in text


def test_unused_user_server_gets_exact_advice() -> None:
    text = "\n".join(render(ON, [_server("idle")], {}, {}, [], 30))
    assert "claude mcp remove idle --scope user" in text and "/mcp" in text


def test_unused_name_is_matched_after_normalization() -> None:
    text = "\n".join(render(ON, [_server("my.server")], {"my_server": 4}, {"my_server": 4}, [], 30))
    assert "4 here" in text and "unused" not in text


def test_organization_server_gets_no_remove_command() -> None:
    text = "\n".join(render(ON, [_server("org", scope="organization")], {}, {}, [], 30))
    assert "managed by your organization" in text and "claude mcp remove" not in text


def test_disabled_server_gets_no_advice() -> None:
    text = "\n".join(render(ON, [_server("off", disabled=True)], {}, {}, [], 30))
    assert "disabled for this project" in text and "claude mcp remove" not in text


def test_tool_search_off_line() -> None:
    assert any(ALL_SCHEMAS in line for line in render(OFF, [], {}, {}, [], 30))
    assert not any(ALL_SCHEMAS in line for line in render(ON, [], {}, {}, [], 30))


def test_plugin_note_and_notes() -> None:
    lines = render(ON, [], {}, {}, ["x is not valid JSON; skipped", "y"], 30)
    assert any("plugins and claude.ai connectors" in line for line in lines)
    assert "note: x is not valid JSON; skipped" in lines and "note: y" in lines


@pytest.fixture
def mcp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(cli, "system_managed", lambda: Managed())
    monkeypatch.setattr(cli, "managed_mcp_path", lambda: tmp_path / "no-org" / "managed-mcp.json")
    for name in (
        "ENABLE_TOOL_SEARCH",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return home


def _tool_use(name: str) -> dict[str, object]:
    return assistant(
        "m1", usage=(1, 1, 1, 1), content=[{"type": "tool_use", "id": "t1", "name": name, "input": {}}]
    )


def test_cli_reports_unused_server_and_writes_nothing(
    mcp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = {"command": "x", "env": {"TOKEN": "SECRET-VALUE"}}
    (mcp_home / ".claude.json").write_text(json.dumps({"mcpServers": {"idle": secret}}), encoding="utf-8")
    write_jsonl(mcp_home / ".claude" / "projects" / "p" / "s.jsonl", [_tool_use("mcp__other__x")])
    before = {p: p.read_bytes() for p in mcp_home.rglob("*") if p.is_file()}
    assert main(["mcp"]) == 0
    out = capsys.readouterr().out
    assert "idle" in out and "claude mcp remove idle --scope user" in out
    assert "SECRET-VALUE" not in out
    assert {p: p.read_bytes() for p in mcp_home.rglob("*") if p.is_file()} == before


def test_cli_with_no_logs_and_no_config(mcp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mcp", "--since", "7d"]) == 0
    out = capsys.readouterr().out
    assert "Tool search: on" in out and "last 7 days" in out


def test_unused_here_but_used_elsewhere() -> None:
    text = "\n".join(render(ON, [_server("shared")], {}, {"shared": 5}, [], 30))
    assert "unused in this project" in text and "/mcp" in text
    assert "claude mcp remove" not in text  # still used in other projects
    assert "0 here" in text and "5 in all projects" in text


def test_project_scope_server_unused_here_can_be_removed() -> None:
    text = "\n".join(render(ON, [_server("p", scope="project")], {}, {"p": 3}, [], 30))
    assert "claude mcp remove p --scope project" in text


def test_cli_counts_this_project_separately(mcp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (mcp_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"srv": {"command": "x"}}}), encoding="utf-8"
    )
    write_jsonl(mcp_home / ".claude" / "projects" / "elsewhere" / "s.jsonl", [_tool_use("mcp__srv__x")])
    assert main(["mcp"]) == 0
    out = capsys.readouterr().out
    assert "unused in this project" in out and "claude mcp remove" not in out

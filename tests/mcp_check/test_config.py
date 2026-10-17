import json
import os
from pathlib import Path

import pytest

from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.mcp_check.config import Server, Sources, read_servers, settings_env_blocks


def _sources(
    tmp_path: Path, environ: dict[str, str] | None = None, managed: Managed | None = None
) -> Sources:
    home = tmp_path / "home"
    project = tmp_path / "proj"
    home.mkdir(exist_ok=True)
    project.mkdir(exist_ok=True)
    return Sources(home, environ or {}, project, managed or Managed(), tmp_path / "org" / "managed-mcp.json")


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def _by_name(servers: list[Server]) -> dict[str, Server]:
    return {s.name: s for s in servers}


def test_user_server_and_transport(tmp_path: Path) -> None:
    src = _sources(tmp_path)
    _write(src.claude_json, {"mcpServers": {"a": {"command": "x"}, "b": {"type": "http", "url": "u"}}})
    servers, notes = read_servers(src)
    found = _by_name(servers)
    assert found["a"] == Server("a", "user", "stdio", False, False)
    assert found["b"].transport == "http"
    assert notes == []


def test_local_server_matches_project_path_spelling(tmp_path: Path) -> None:
    src = _sources(tmp_path)
    spelled = str(src.project).replace("\\", "/") + "/"
    if os.name == "nt":
        spelled = spelled[0].swapcase() + spelled[1:]
    _write(src.claude_json, {"projects": {spelled: {"mcpServers": {"loc": {"command": "y"}}}}})
    servers, _ = read_servers(src)
    assert _by_name(servers)["loc"].scope == "local"


def test_disabled_user_server_for_project(tmp_path: Path) -> None:
    src = _sources(tmp_path)
    key = str(src.project).replace("\\", "/")
    _write(
        src.claude_json,
        {"mcpServers": {"x": {"command": "x"}}, "projects": {key: {"disabledMcpServers": ["x"]}}},
    )
    assert _by_name(read_servers(src)[0])["x"].disabled


def test_project_mcp_json_and_rejected(tmp_path: Path) -> None:
    src = _sources(tmp_path)
    _write(
        src.project / ".mcp.json",
        {"mcpServers": {"p": {"type": "stdio", "command": "p"}, "q": {"command": "q"}}},
    )
    key = str(src.project).replace("\\", "/")
    _write(src.claude_json, {"projects": {key: {"disabledMcpjsonServers": ["q"]}}})
    found = _by_name(read_servers(src)[0])
    assert found["p"].scope == "project" and not found["p"].disabled
    assert found["q"].disabled


def test_organization_servers(tmp_path: Path) -> None:
    managed = Managed(documents=[{"managedMcpServers": {"m2": {"type": "http", "url": "https://x"}}}])
    src = _sources(tmp_path, managed=managed)
    _write(src.managed_mcp, {"mcpServers": {"m1": {"command": "m"}}})
    found = _by_name(read_servers(src)[0])
    assert found["m1"].scope == "organization" and found["m2"].scope == "organization"


def test_always_load(tmp_path: Path) -> None:
    src = _sources(tmp_path)
    _write(src.claude_json, {"mcpServers": {"a": {"command": "x", "alwaysLoad": True}}})
    assert _by_name(read_servers(src)[0])["a"].always_load


def test_claude_config_dir(tmp_path: Path) -> None:
    custom = tmp_path / "cfg"
    src = _sources(tmp_path, environ={"CLAUDE_CONFIG_DIR": str(custom)})
    _write(custom / ".claude.json", {"mcpServers": {"c": {"command": "x"}}})
    _write(src.home / ".claude.json", {"mcpServers": {"home": {"command": "x"}}})
    assert set(_by_name(read_servers(src)[0])) == {"c"}


@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        ("{not json", "not valid JSON"),
        ('{"mcpServers": []}', "not a JSON object"),
        ("[1]", "not a JSON object"),
    ],
)
def test_malformed_claude_json_is_a_note(tmp_path: Path, content: str, fragment: str) -> None:
    src = _sources(tmp_path)
    _write(src.claude_json, content)
    _write(src.project / ".mcp.json", {"mcpServers": {"p": {"command": "p"}}})
    servers, notes = read_servers(src)
    assert [s.name for s in servers] == ["p"]
    assert len(notes) == 1 and fragment in notes[0]


def test_server_entry_not_an_object(tmp_path: Path) -> None:
    src = _sources(tmp_path)
    _write(src.claude_json, {"mcpServers": {"bad": "x", "good": {"command": "y"}}})
    servers, notes = read_servers(src)
    assert [s.name for s in servers] == ["good"]
    assert "bad" in notes[0]


def test_secrets_are_never_kept(tmp_path: Path) -> None:
    src = _sources(tmp_path)
    entry = {"command": "x", "env": {"TOKEN": "SECRET-VALUE"}, "headers": {"A": "SECRET-VALUE"}}
    _write(src.claude_json, {"mcpServers": {"a": entry}})
    servers, notes = read_servers(src)
    assert "SECRET-VALUE" not in repr(servers) + repr(notes)


def test_settings_env_blocks_order(tmp_path: Path) -> None:
    managed = Managed(documents=[{"env": {"A": "managed"}}])
    src = _sources(tmp_path, managed=managed)
    _write(src.home / ".claude" / "settings.json", {"env": {"A": "user"}})
    _write(src.project / ".claude" / "settings.json", {"env": {"A": "project"}})
    _write(src.project / ".claude" / "settings.local.json", {"env": {"A": "local"}})
    blocks = settings_env_blocks(src)
    assert [b["A"] for b in blocks] == ["user", "project", "local", "managed"]


def test_missing_files_are_silent(tmp_path: Path) -> None:
    assert read_servers(_sources(tmp_path)) == ([], [])

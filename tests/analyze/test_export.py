import json
from pathlib import Path

from ai_usage_kit.analyze.export import build_export, export_model_name, write_export
from ai_usage_kit.analyze.runner import analyze
from tests.analyze.fixture_tree import build_fixture_tree

SENSITIVE = [
    "SECRET-PROMPT",
    "secret-repo",
    "secret_module",
    "secret_tests",
    "secretsrv",
    "secret instructions",
    "C--work",
    "sess-a",
    "sess-b",
    "agent-1",
    "config.py",
    "arn:aws",
    "123456789012",
    "alpha",
    "other-repo",
]


def test_export_numbers(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    data = build_export(analyze(root, home), "0.1.0")
    assert data["schema"] == 1
    assert isinstance(data["export_id"], str)
    assert (data["date_from"], data["date_to"]) == ("2026-10-01", "2026-10-02")
    assert (data["sessions"], data["api_calls"], data["user_prompts"], data["compactions"]) == (2, 9, 2, 1)
    assert data["tokens"] == {
        "main": {"input": 135, "output": 130, "cache_read": 6290, "cache_write": 5450},
        "sub": {"input": 55, "output": 15, "cache_read": 550, "cache_write": 500},
        "all": {"input": 190, "output": 145, "cache_read": 6840, "cache_write": 5950},
    }
    assert data["rules"]["cache-rebuild"] == {"hits": 1, "exact_tokens": 2300, "est_tokens": 0}  # type: ignore[index]
    assert data["tools"] == {"Bash": 1, "Edit": 1, "Read": 5}
    assert data["models"] == {"claude-opus-5-5": 2, "claude-sonnet-5": 6, "other": 1}
    assert data["mcp_server_count"] == 4
    assert data["claude_code_versions"] == ["2.1.290"]
    assert data["parse"] == {"files": 3, "unreadable_files": 0, "bad_lines": 1, "unknown_records": 1}


def test_export_contains_no_names_paths_or_text(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    out = tmp_path / "export.json"
    write_export(out, build_export(analyze(root, home), "0.1.0"))
    dumped = out.read_text(encoding="utf-8")
    for marker in [*SENSITIVE, str(tmp_path), tmp_path.name, json.dumps(str(tmp_path))[1:-1]]:
        assert marker not in dumped, marker


def test_export_maps_non_builtin_tool_names_to_other(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    a = analyze(root, home)
    a.sessions[0].tool_calls[0].name = "AcmeInternalLookup"
    data = build_export(a, "0.1.0")
    assert "AcmeInternalLookup" not in json.dumps(data)
    assert data["tools"] == {"Edit": 1, "Read": 4, "Bash": 1, "other": 1}


def test_export_model_names() -> None:
    assert export_model_name("claude-sonnet-5") == "claude-sonnet-5"
    assert export_model_name("claude-opus-4-6[1m]") == "claude-opus-4-6"
    assert export_model_name("claude-opus-4@20250514") == "claude-opus-4"
    assert export_model_name("us.anthropic.claude-sonnet-4-5-20250929-v1:0") == "claude-sonnet-4-5-20250929"
    assert export_model_name("anthropic.claude-3-haiku-20240307-v1:0") == "claude-3-haiku-20240307"
    assert export_model_name("arn:aws:bedrock:us-east-1:123456789012:inference-profile/x") == "other"
    assert export_model_name("claude-Custom") == "other"
    assert export_model_name("gpt-4o") == "other"


def test_export_has_per_export_id_only(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    a = analyze(root, home)
    first, second = build_export(a, "0.1.0"), build_export(a, "0.1.0")
    assert "install_id" not in first
    assert first["export_id"] != second["export_id"]

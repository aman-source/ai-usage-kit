import json
from pathlib import Path

from ai_usage_kit.defaults.managed import Managed, managed_dir, read_managed, windows_registry


def _none() -> list[tuple[str, str]]:
    return []


def test_reads_file_and_drop_in_directory(tmp_path: Path) -> None:
    (tmp_path / "managed-settings.d").mkdir()
    team = json.dumps({"effortLevel": "high"})
    (tmp_path / "managed-settings.d" / "10-team.json").write_text(team, "utf-8")
    policy = json.dumps({"permissions": {"deny": ["Read(.env)"]}})
    (tmp_path / "managed-settings.json").write_text(policy, "utf-8")
    managed = read_managed(tmp_path, _none)
    assert managed.sets("effortLevel")
    assert managed.sets("permissions.deny")
    assert not managed.sets("autoCompactWindow")
    assert len(managed.sources) == 2


def test_registry_documents_count(tmp_path: Path) -> None:
    managed = read_managed(tmp_path / "absent", lambda: [("HKLM\\x", '{"autoCompactWindow": 300000}')])
    assert managed.sets("autoCompactWindow")
    assert managed.sources == ["HKLM\\x"]


def test_bad_documents_are_reported_not_raised(tmp_path: Path) -> None:
    (tmp_path / "managed-settings.json").write_text("{bad", "utf-8")
    managed = read_managed(tmp_path, lambda: [("HKLM\\x", "[1]")])
    assert managed.documents == []
    assert managed.unreadable == [str(tmp_path / "managed-settings.json"), "HKLM\\x"]


def test_empty_view_sets_nothing() -> None:
    assert not Managed().sets("effortLevel")


def test_managed_dir_per_platform() -> None:
    assert managed_dir("win32") == Path(r"C:\Program Files\ClaudeCode")
    assert managed_dir("darwin") == Path("/Library/Application Support/ClaudeCode")
    assert managed_dir("linux") == Path("/etc/claude-code")


def test_windows_registry_read_is_a_list() -> None:
    assert isinstance(windows_registry(), list)

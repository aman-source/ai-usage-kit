import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_usage_kit.analyze.versions import newest_claude_code_version


def _session(root: Path, project: str, name: str, versions: list[str], age_days: float) -> None:
    folder = root / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    lines = [json.dumps({"type": "user", "version": v, "message": {"content": "x"}}) for v in versions]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stamp = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))


def test_newest_version_is_numeric_max_of_recent_sessions(tmp_path: Path) -> None:
    _session(tmp_path, "p", "a", ["2.1.9", "2.1.251"], 1)
    _session(tmp_path, "p", "b", ["2.1.263"], 2)
    _session(tmp_path, "q", "c", ["9.9.9"], 60)  # older than the window
    since = datetime.now(UTC) - timedelta(days=30)
    assert newest_claude_code_version(tmp_path, since) == "2.1.263"


def test_no_logs_gives_none(tmp_path: Path) -> None:
    assert newest_claude_code_version(tmp_path / "missing", datetime.now(UTC)) is None


def test_non_version_strings_are_ignored(tmp_path: Path) -> None:
    _session(tmp_path, "p", "a", ["dev", "2.1.300-beta"], 1)
    since = datetime.now(UTC) - timedelta(days=30)
    assert newest_claude_code_version(tmp_path, since) is None

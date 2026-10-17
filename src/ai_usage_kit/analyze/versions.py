"""The newest Claude Code version recorded in recent local session logs."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ai_usage_kit.analyze.discover import SessionFiles, discover
from ai_usage_kit.analyze.parse import parse_file
from ai_usage_kit.defaults.items import version_key

_RELEASE = re.compile(r"\d+\.\d+\.\d+")
NEWEST_SESSIONS = 5


def newest_claude_code_version(root: Path, since: datetime) -> str | None:
    """Only plain release versions (x.y.z) count; the newest few sessions are enough."""
    sessions = discover(root, None, since)
    sessions.sort(key=_modified, reverse=True)
    found: set[str] = set()
    for files in sessions[:NEWEST_SESSIONS]:
        for path in files.paths():
            found |= parse_file(path)[1].versions
    releases = [v for v in found if _RELEASE.fullmatch(v)]
    return max(releases, key=version_key) if releases else None


def _modified(files: SessionFiles) -> float:
    try:
        return files.last_modified().timestamp()
    except OSError:
        return 0.0

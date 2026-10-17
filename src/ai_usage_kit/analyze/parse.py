"""Read one Claude Code session file into raw records. Bad data is counted, never raised."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Record = dict[str, Any]

# Record types seen in Claude Code 2.1.263-2.1.296 logs. Anything else is counted and skipped.
KNOWN_TYPES = frozenset(
    {
        "assistant",
        "user",
        "attachment",
        "system",
        "summary",
        "queue-operation",
        "atis-latch",
        "last-prompt",
        "bridge-session",
        "ai-title",
        "mode",
        "pr-link",
        "file-history-delta",
        "frame-link",
        "file-history-snapshot",
        "artifact-autoreact-ledger",
        "artifact-comment-monitor",
        "cost-state",
        "teleported-from",
    }
)


@dataclass
class ParseStats:
    files: int = 0
    lines: int = 0
    bad_lines: int = 0
    unknown_types: Counter[str] = field(default_factory=Counter)
    versions: set[str] = field(default_factory=set)
    unreadable: list[Path] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return self.bad_lines + sum(self.unknown_types.values())

    def merge(self, other: ParseStats) -> None:
        self.files += other.files
        self.lines += other.lines
        self.bad_lines += other.bad_lines
        self.unknown_types.update(other.unknown_types)
        self.versions |= other.versions
        self.unreadable.extend(other.unreadable)


def parse_file(path: Path) -> tuple[list[Record], ParseStats]:
    stats = ParseStats()
    records: list[Record] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    _parse_line(line, records, stats)
    except OSError:
        return [], ParseStats(unreadable=[path])
    stats.files = 1
    return records, stats


def _parse_line(line: str, records: list[Record], stats: ParseStats) -> None:
    stats.lines += 1
    try:
        record = json.loads(line)
    except ValueError:
        stats.bad_lines += 1
        return
    if not isinstance(record, dict):
        stats.bad_lines += 1
        return
    kind = record.get("type")
    if kind not in KNOWN_TYPES:
        stats.unknown_types[str(kind)] += 1
        return
    version = record.get("version")
    if isinstance(version, str):
        stats.versions.add(version)
    records.append(record)

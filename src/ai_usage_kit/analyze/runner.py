"""Run the analyzer end to end: discover -> parse -> model -> rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_usage_kit.analyze.discover import discover
from ai_usage_kit.analyze.model import MAIN, Session, build_session
from ai_usage_kit.analyze.parse import ParseStats, Record, parse_file
from ai_usage_kit.analyze.rules import (
    Finding,
    ReadText,
    Thresholds,
    big_instructions,
    read_text,
    run_session_rules,
)


@dataclass
class Analysis:
    root: Path
    sessions: list[Session]
    findings: list[Finding]
    stats: ParseStats


def analyze(
    root: Path,
    home: Path,
    *,
    project: str | None = None,
    since: datetime | None = None,
    thresholds: Thresholds | None = None,
    read: ReadText = read_text,
) -> Analysis:
    t = thresholds or Thresholds()
    stats = ParseStats()
    sessions: list[Session] = []
    findings: list[Finding] = []
    for files in discover(root, project, since, unreadable=stats.unreadable):
        threads: list[tuple[str, list[Record]]] = []
        for path in files.paths():
            records, file_stats = parse_file(path)
            stats.merge(file_stats)
            threads.append((MAIN if path == files.main else path.stem, records))
        session = build_session(files.project, files.session_id, threads)
        if not session.calls:
            continue
        sessions.append(session)
        findings.extend(run_session_rules(session, t))
    findings.extend(big_instructions({s.cwd for s in sessions if s.cwd}, home, t, read))
    return Analysis(root, sessions, findings, stats)

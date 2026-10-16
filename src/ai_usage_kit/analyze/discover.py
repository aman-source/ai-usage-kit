"""Find session files: <root>/<project>/<session>.jsonl plus <root>/<project>/<session>/subagents/*.jsonl."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class SessionFiles:
    project: str
    session_id: str
    main: Path | None
    subagents: tuple[Path, ...]

    def paths(self) -> tuple[Path, ...]:
        return ((self.main,) if self.main else ()) + self.subagents

    def last_modified(self) -> datetime:
        return max(datetime.fromtimestamp(p.stat().st_mtime, UTC) for p in self.paths())


def discover(
    root: Path,
    project: str | None = None,
    since: datetime | None = None,
    unreadable: list[Path] | None = None,
) -> list[SessionFiles]:
    """Folders or files that cannot be read are appended to `unreadable` and skipped."""
    skipped = unreadable if unreadable is not None else []
    if not root.is_dir():
        return []
    found: list[SessionFiles] = []
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if project and project.lower() not in project_dir.name.lower():
            continue
        try:
            sessions = _sessions(project_dir)
        except OSError:
            skipped.append(project_dir)
            continue
        for files in sessions:
            try:
                recent = since is None or files.last_modified() >= since
            except OSError:
                skipped.extend(files.paths())
                continue
            if recent:
                found.append(files)
    return found


def _sessions(project_dir: Path) -> list[SessionFiles]:
    ids = {p.stem for p in project_dir.glob("*.jsonl")}
    ids |= {p.name for p in project_dir.iterdir() if (p / "subagents").is_dir()}
    sessions: list[SessionFiles] = []
    for session_id in sorted(ids):
        main = project_dir / f"{session_id}.jsonl"
        subagents = tuple(sorted((project_dir / session_id / "subagents").glob("*.jsonl")))
        files = SessionFiles(project_dir.name, session_id, main if main.is_file() else None, subagents)
        if files.paths():
            sessions.append(files)
    return sessions

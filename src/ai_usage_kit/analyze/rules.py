"""Waste rules. Each rule is a pure function returning findings with their evidence."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path, PurePath

from ai_usage_kit.analyze.model import Session, ToolCall, mcp_server

FIX = {
    "repeated-read": "read-once",
    "large-tool-output": "trim",
    "cache-rebuild": "defaults",
    "big-instructions": "defaults",
    "many-mcp": "eai mcp",
    "long-session": "defaults",
}
RULES = tuple(FIX)
CHARS_PER_TOKEN = 4
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


@dataclass(frozen=True)
class Thresholds:
    large_output_chars: int = 20_000
    cache_gap: timedelta = timedelta(minutes=5)
    cache_read_ratio: float = 0.5
    instruction_lines: int = 200
    mcp_servers: int = 3
    long_session_calls: int = 200
    long_session_context: int = 150_000


@dataclass(frozen=True)
class Finding:
    rule: str
    project: str
    session_id: str
    when: datetime | None
    evidence: str
    exact_tokens: int = 0
    chars: int = 0

    @property
    def est_tokens(self) -> int:
        return self.chars // CHARS_PER_TOKEN

    @property
    def fix(self) -> str:
        return FIX[self.rule]


def rule_totals(findings: Iterable[Finding]) -> dict[str, tuple[int, int, int]]:
    totals = {rule: (0, 0, 0) for rule in RULES}
    for f in findings:
        hits, exact, est = totals[f.rule]
        totals[f.rule] = (hits + 1, exact + f.exact_tokens, est + f.est_tokens)
    return totals


def tool_label(call: ToolCall) -> str:
    if call.name in SHELL_TOOLS:
        command = call.input.get("command")
        words = command.split() if isinstance(command, str) else []
        return f"{call.name}:{PurePath(words[0]).name if words else '?'}"
    server = mcp_server(call.name)
    return f"mcp:{server}" if server else call.name


def repeated_read(session: Session, t: Thresholds) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[tuple[str, str, str, str], int] = {}
    for call in session.tool_calls:
        edited = _edited_path(call)
        if edited is not None:
            for key in [k for k in seen if k[0] == call.thread and k[1] == edited]:
                del seen[key]
            continue
        path = call.input.get("file_path")
        if call.name != "Read" or not isinstance(path, str):
            continue
        key = (call.thread, path, _start_line(call.input.get("offset")), str(call.input.get("limit")))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            findings.append(
                Finding(
                    "repeated-read",
                    session.project,
                    session.session_id,
                    call.timestamp,
                    f"{path} read {seen[key]}x [{call.thread}]",
                    chars=call.result_chars,
                )
            )
    return findings


def large_tool_output(session: Session, t: Thresholds) -> list[Finding]:
    return [
        Finding(
            "large-tool-output",
            session.project,
            session.session_id,
            call.timestamp,
            f"{tool_label(call)} returned {call.result_chars:,} chars [{call.thread}]",
            chars=call.result_chars,
        )
        for call in session.tool_calls
        if call.result_chars > t.large_output_chars
    ]


def _start_line(offset: object) -> str:
    """Read offsets are 1-based; no offset (or 0) means line 1."""
    return "1" if offset in (None, 0) else str(offset)


def _edited_path(call: ToolCall) -> str | None:
    if call.name not in EDIT_TOOLS:
        return None
    path = call.input.get("file_path", call.input.get("notebook_path"))
    return path if isinstance(path, str) else None


def cache_rebuild(session: Session, t: Thresholds) -> list[Finding]:
    timed = [(c.timestamp, c) for c in session.main_calls() if c.timestamp is not None]
    findings: list[Finding] = []
    for (prev_at, prev), (at, call) in pairwise(timed):
        gap = at - prev_at
        cold = call.usage.cache_read < t.cache_read_ratio * prev.usage.context
        rebuilt = cold and call.usage.cache_write > 0
        if gap > t.cache_gap and rebuilt:
            minutes = gap.total_seconds() / 60
            findings.append(
                Finding(
                    "cache-rebuild",
                    session.project,
                    session.session_id,
                    at,
                    f"idle {minutes:.0f} min, then {call.usage.cache_write:,} tokens written to cache",
                    exact_tokens=call.usage.cache_write,
                )
            )
    return findings


def long_session(session: Session, t: Thresholds) -> list[Finding]:
    main = session.main_calls()
    peak = max((c.usage.context for c in main), default=0)
    if len(main) <= t.long_session_calls and peak <= t.long_session_context:
        return []
    over = sum(c.usage.cache_read for c in main if c.usage.context > t.long_session_context)
    return [
        Finding(
            "long-session",
            session.project,
            session.session_id,
            session.start,
            f"{len(main)} API calls, peak context {peak:,} tokens",
            exact_tokens=over,
        )
    ]


def many_mcp(session: Session, t: Thresholds) -> list[Finding]:
    if len(session.mcp_servers) <= t.mcp_servers:
        return []
    names = ", ".join(sorted(session.mcp_servers))
    return [
        Finding(
            "many-mcp",
            session.project,
            session.session_id,
            session.start,
            f"{len(session.mcp_servers)} MCP servers announced ({names}); "
            f"first-call context {session.prefix_size:,} tokens",
        )
    ]


def run_session_rules(session: Session, t: Thresholds) -> list[Finding]:
    return [
        *repeated_read(session, t),
        *large_tool_output(session, t),
        *cache_rebuild(session, t),
        *many_mcp(session, t),
        *long_session(session, t),
    ]


ReadText = Callable[[Path], str | None]


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def instruction_files(cwds: Iterable[str], home: Path) -> list[Path]:
    paths = {home / ".claude" / "CLAUDE.md"}
    for cwd in cwds:
        paths |= {Path(cwd) / "CLAUDE.md", Path(cwd) / ".claude" / "CLAUDE.md"}
    return sorted(paths)


def big_instructions(
    cwds: Iterable[str], home: Path, t: Thresholds, read: ReadText = read_text
) -> list[Finding]:
    findings: list[Finding] = []
    for path in instruction_files(cwds, home):
        text = read(path)
        if text is None:
            continue
        lines = len(text.splitlines())
        if lines > t.instruction_lines:
            size = len(text.encode("utf-8"))
            findings.append(
                Finding(
                    "big-instructions",
                    "",
                    "",
                    None,
                    f"{path}: {lines} lines, {size:,} bytes (size on disk today)",
                )
            )
    return findings

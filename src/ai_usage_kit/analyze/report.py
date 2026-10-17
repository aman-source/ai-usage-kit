"""Render an Analysis as a terminal summary or a Markdown report. ASCII only, deterministic order."""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Sequence

from ai_usage_kit.analyze.model import Session, total_usage
from ai_usage_kit.analyze.parse import ParseStats
from ai_usage_kit.analyze.rules import FIX, RULES, Finding, rule_totals
from ai_usage_kit.analyze.runner import Analysis

TOKEN_ROWS = (
    ("input", "input"),
    ("output", "output"),
    ("cache read", "cache_read"),
    ("cache write", "cache_write"),
)
LOCAL_ONLY_NOTE = (
    "Local use only: this report names local paths, projects and sessions. "
    "To share numbers, use `eai analyze --export`, which contains no names or paths."
)
EST_NOTE = (
    "exact = from API usage; est. = characters / 4. Exact and estimated numbers are never added together."
)


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    text = [[_cell(v) for v in row] for row in rows]
    numeric = [bool(rows) and all(isinstance(row[i], int) for row in rows) for i in range(len(headers))]
    widths = [max([_width(headers[i])] + [_width(r[i]) for r in text]) for i in range(len(headers))]

    def fmt(cells: Sequence[str]) -> str:
        pads = (" " * (w - _width(c)) for c, w in zip(cells, widths, strict=True))
        parts = (p + c if n else c + p for c, p, n in zip(cells, pads, numeric, strict=True))
        return "| " + " | ".join(parts) + " |"

    dashes = ("-" * (w + 1) + ":" if n else "-" * (w + 2) for w, n in zip(widths, numeric, strict=True))
    return [fmt(headers), "|" + "|".join(dashes) + "|", *(fmt(r) for r in text)]


def render_terminal(a: Analysis, top: int = 10) -> str:
    lines = [
        f"ai-usage-kit analyze: {_header(a)}",
        "",
        *_tokens(a),
        "",
        _counts(a),
        "",
        f"Waste ({EST_NOTE})",
        *_waste(a),
        "",
        f"Top {top} sessions by total tokens",
        *_top_table(_top(a, top)),
        "",
        _health(a.stats),
    ]
    return "\n".join(lines)


def render_markdown(a: Analysis, top: int = 10) -> str:
    sessions = _top(a, top)
    lines = [
        "# Token usage report",
        "",
        LOCAL_ONLY_NOTE,
        "",
        _header(a),
        "",
        "## Tokens",
        "",
        *_tokens(a),
        "",
        _counts(a),
        "",
        "## Waste",
        "",
        EST_NOTE,
        "",
        *_waste(a),
        "",
        f"## Top {top} sessions",
        "",
        *_top_table(sessions),
        "",
        *(_session_line(s) for s in sessions),
        "",
        "## Tool calls",
        "",
        *_tool_table(a),
        "",
        "## Findings",
        "",
        *_findings(a.findings),
        "## Parse health",
        "",
        _health(a.stats),
        "",
    ]
    return "\n".join(lines)


def _width(text: str) -> int:
    """Display width: East Asian wide and fullwidth characters take two columns."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _cell(value: object) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return "-" if value is None else str(value)


def _header(a: Analysis) -> str:
    starts = [start for s in a.sessions if (start := s.start) is not None]
    span = f"{min(starts):%Y-%m-%d} to {max(starts):%Y-%m-%d}" if starts else "no dates"
    projects = len({s.project for s in a.sessions})
    return f"{len(a.sessions)} sessions in {projects} projects, {span}"


def _tokens(a: Analysis) -> list[str]:
    main, sub, both = total_usage(a.sessions, "main"), total_usage(a.sessions, "sub"), total_usage(a.sessions)
    rows = [(label, getattr(main, f), getattr(sub, f), getattr(both, f)) for label, f in TOKEN_ROWS]
    return table(["tokens", "main", "subagents", "total"], rows)


def _counts(a: Analysis) -> str:
    calls = sum(len(s.calls) for s in a.sessions)
    prompts = sum(s.user_prompts for s in a.sessions)
    compactions = sum(s.compactions for s in a.sessions)
    return f"{calls:,} API calls, {prompts:,} user prompts, {compactions:,} compactions"


def _waste(a: Analysis) -> list[str]:
    totals = rule_totals(a.findings)
    rows = [(rule, *totals[rule], FIX[rule]) for rule in RULES]
    return table(["rule", "hits", "exact tokens", "est. tokens", "fix"], rows)


def _top(a: Analysis, top: int) -> list[Session]:
    return sorted(a.sessions, key=lambda s: (-s.usage().total, s.project, s.session_id))[:top]


def _top_table(sessions: Sequence[Session]) -> list[str]:
    rows = [
        (
            s.project,
            s.session_id,
            f"{start:%Y-%m-%d}" if (start := s.start) else None,
            s.user_prompts,
            len(s.calls),
            s.compactions,
            s.usage().total,
            s.prefix_size,
            s.peak_context,
        )
        for s in sessions
    ]
    headers = [
        "project",
        "session",
        "date",
        "prompts",
        "api calls",
        "compactions",
        "total tokens",
        "first-call context",
        "peak context",
    ]
    return table(headers, rows)


def _session_line(s: Session) -> str:
    return (
        f"- {s.session_id}: main {s.usage('main').total:,} / subagents {s.usage('sub').total:,} tokens; "
        f"models {', '.join(s.models)}; tools {_tool_summary(s.tool_counts())}"
    )


def _by_count(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _tool_summary(counts: Counter[str]) -> str:
    return ", ".join(f"{name} {n}" for name, n in _by_count(counts)) or "-"


def _tool_table(a: Analysis) -> list[str]:
    counts: Counter[str] = Counter()
    for s in a.sessions:
        counts.update(s.tool_counts())
    return table(["tool", "calls"], _by_count(counts))


def _findings(findings: Sequence[Finding]) -> list[str]:
    lines: list[str] = []
    for rule in RULES:
        hits = sorted(
            (f for f in findings if f.rule == rule),
            key=lambda f: (-(f.exact_tokens + f.est_tokens), f.project, f.session_id, f.evidence),
        )
        if hits:
            lines += [f"### {rule} (fix: {FIX[rule]})", "", *(_finding_line(f) for f in hits), ""]
    return lines or ["No findings.", ""]


def _finding_line(f: Finding) -> str:
    where = f"{f.project}/{f.session_id}" if f.session_id else "-"
    when = f"{f.when:%Y-%m-%d %H:%M}" if f.when else "-"
    size = ""
    if f.exact_tokens:
        size = f" (exact {f.exact_tokens:,} tokens)"
    elif f.chars:
        size = f" ({f.chars:,} chars, est. {f.est_tokens:,} tokens)"
    return f"- {where} {when}: {f.evidence}{size}"


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))


def _health(stats: ParseStats) -> str:
    versions = sorted(stats.versions, key=_version_key)
    span = f"{versions[0]} to {versions[-1]}" if versions else "unknown"
    unknown = ", ".join(f"{k} x{v}" for k, v in sorted(stats.unknown_types.items())) or "none"
    return (
        f"Parsed {stats.files:,} files ({len(stats.unreadable)} unreadable), {stats.lines:,} lines; "
        f"skipped {stats.bad_lines:,} bad lines and {sum(stats.unknown_types.values()):,} unknown records "
        f"({unknown}); Claude Code {span}"
    )

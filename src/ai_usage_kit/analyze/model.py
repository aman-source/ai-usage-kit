"""Build a Session from parsed records: one ApiCall per message.id, tool calls paired with their results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from ai_usage_kit.analyze.parse import Record

MAIN = "main"
SYNTHETIC_MODEL = "<synthetic>"
Scope = Literal["all", "main", "sub"]
# User records Claude Code writes itself (not typed by a person), seen in logs that lack `origin`.
GENERATED_PREFIXES = (
    "<task-notification>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "[Request interrupted",
)


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def from_api(cls, raw: object) -> Usage:
        if not isinstance(raw, dict):
            return cls()

        def count(key: str) -> int:
            value = raw.get(key)
            return value if isinstance(value, int) else 0

        return cls(
            count("input_tokens"),
            count("output_tokens"),
            count("cache_read_input_tokens"),
            count("cache_creation_input_tokens"),
        )

    @property
    def context(self) -> int:
        return self.input + self.cache_read + self.cache_write

    @property
    def total(self) -> int:
        return self.context + self.output

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input + other.input,
            self.output + other.output,
            self.cache_read + other.cache_read,
            self.cache_write + other.cache_write,
        )


@dataclass(frozen=True)
class ApiCall:
    message_id: str
    model: str
    timestamp: datetime | None
    usage: Usage
    thread: str


@dataclass
class ToolCall:
    tool_use_id: str
    name: str
    input: dict[str, Any]
    timestamp: datetime | None
    thread: str
    result_chars: int = 0


@dataclass
class Session:
    project: str
    session_id: str
    cwd: str | None = None
    calls: list[ApiCall] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    user_prompts: int = 0
    compactions: int = 0
    mcp_servers: set[str] = field(default_factory=set)

    def main_calls(self) -> list[ApiCall]:
        return [c for c in self.calls if c.thread == MAIN]

    def usage(self, scope: Scope = "all") -> Usage:
        total = Usage()
        for call in self.calls:
            if scope == "all" or (call.thread == MAIN) == (scope == "main"):
                total += call.usage
        return total

    def tool_counts(self) -> Counter[str]:
        return Counter(tool_group(c.name) for c in self.tool_calls)

    @property
    def prefix_size(self) -> int:
        calls = self.main_calls() or self.calls
        return calls[0].usage.context if calls else 0

    @property
    def peak_context(self) -> int:
        return max((c.usage.context for c in self.calls), default=0)

    @property
    def start(self) -> datetime | None:
        return min((c.timestamp for c in self.calls if c.timestamp is not None), default=None)

    @property
    def models(self) -> list[str]:
        return sorted({c.model for c in self.calls})


def total_usage(sessions: Iterable[Session], scope: Scope = "all") -> Usage:
    total = Usage()
    for session in sessions:
        total += session.usage(scope)
    return total


def mcp_server(tool_name: str) -> str | None:
    parts = tool_name.split("__")
    return parts[1] if len(parts) >= 3 and parts[0] == "mcp" and parts[1] else None


def tool_group(tool_name: str) -> str:
    server = mcp_server(tool_name)
    return f"mcp:{server}" if server else tool_name


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_session(project: str, session_id: str, threads: Sequence[tuple[str, Sequence[Record]]]) -> Session:
    session = Session(project, session_id)
    for thread, records in threads:
        _ThreadReader(session, thread).read(records)
    return session


class _ThreadReader:
    """Reads one file (main thread or one subagent) into the shared Session."""

    def __init__(self, session: Session, thread: str) -> None:
        self.session = session
        self.thread = thread
        self.calls: dict[str, ApiCall] = {}
        self.tools: dict[str, ToolCall] = {}

    def read(self, records: Sequence[Record]) -> None:
        for index, record in enumerate(records):
            cwd = record.get("cwd")
            if self.session.cwd is None and isinstance(cwd, str):
                self.session.cwd = cwd
            kind = record.get("type")
            if kind == "assistant":
                self._assistant(record, index)
            elif kind == "user":
                self._user(record)
            elif kind == "attachment":
                self._attachment(record)
            elif kind == "system" and record.get("subtype") == "compact_boundary" and self.thread == MAIN:
                self.session.compactions += 1
        self.session.calls.extend(self.calls.values())
        self.session.tool_calls.extend(self.tools.values())

    def _assistant(self, record: Record, index: int) -> None:
        message = record.get("message")
        if not isinstance(message, dict):
            return
        timestamp = parse_timestamp(record.get("timestamp"))
        model = message.get("model")
        if isinstance(model, str) and model != SYNTHETIC_MODEL:
            message_id = message.get("id")
            key = message_id if isinstance(message_id, str) else f"{self.thread}#{index}"
            first = self.calls.get(key)
            self.calls[key] = ApiCall(
                key,
                model,
                first.timestamp if first else timestamp,
                Usage.from_api(message.get("usage")),
                self.thread,
            )
        for block in _blocks(message.get("content")):
            if block.get("type") == "tool_use":
                self._tool_use(block, timestamp)

    def _tool_use(self, block: dict[str, Any], timestamp: datetime | None) -> None:
        tool_id, name, tool_input = block.get("id"), block.get("name"), block.get("input")
        if not isinstance(tool_id, str) or not isinstance(name, str) or tool_id in self.tools:
            return
        self.tools[tool_id] = ToolCall(
            tool_id, name, tool_input if isinstance(tool_input, dict) else {}, timestamp, self.thread
        )
        server = mcp_server(name)
        if server:
            self.session.mcp_servers.add(server)

    def _user(self, record: Record) -> None:
        message = record.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content")
        for block in _blocks(content):
            if block.get("type") == "tool_result":
                call = self.tools.get(str(block.get("tool_use_id")))
                if call is not None:
                    call.result_chars = _result_chars(block.get("content"))
        if self.thread == MAIN and _is_prompt(record, content):
            self.session.user_prompts += 1

    def _attachment(self, record: Record) -> None:
        attachment = record.get("attachment")
        if not isinstance(attachment, dict):
            return
        kind = attachment.get("type")
        names: list[object] = []
        if kind == "deferred_tools_delta":
            names = _list(attachment.get("addedNames"))
        elif kind == "deferred_tools_record":
            names = [e.get("name") for e in _list(attachment.get("entries")) if isinstance(e, dict)]
            names += _list(attachment.get("nameOnlyAnnouncements"))
        for name in names:
            server = mcp_server(name) if isinstance(name, str) else None
            if server:
                self.session.mcp_servers.add(server)


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _blocks(content: object) -> list[dict[str, Any]]:
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_chars(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    return sum(len(b["text"]) for b in _blocks(content) if isinstance(b.get("text"), str))


def _is_prompt(record: Record, content: object) -> bool:
    if record.get("isMeta") or record.get("isCompactSummary") or record.get("isSidechain"):
        return False
    origin = record.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    # Logs without `origin`: typed text only, minus records Claude Code generates itself.
    if isinstance(content, str):
        return bool(content.strip()) and not content.lstrip().startswith(GENERATED_PREFIXES)
    blocks = _blocks(content)
    texts = [b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)]
    return (
        bool(texts)
        and not any(b.get("type") == "tool_result" for b in blocks)
        and not texts[0].lstrip().startswith(GENERATED_PREFIXES)
    )

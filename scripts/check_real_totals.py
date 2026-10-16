"""Local check on real logs: analyzer totals must match an independent per-message.id count.

Prints numbers only. Run: uv run python scripts/check_real_totals.py [ROOT]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ai_usage_kit.analyze.model import total_usage
from ai_usage_kit.analyze.runner import analyze

KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def independent_totals(root: Path) -> tuple[int, ...]:
    totals = [0, 0, 0, 0]
    for path in sorted(root.glob("*/*.jsonl")) + sorted(root.glob("*/*/subagents/*.jsonl")):
        last: dict[str, dict[str, object]] = {}
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                message = record.get("message")
                if (
                    record.get("type") == "assistant"
                    and isinstance(message, dict)
                    and message.get("model") != "<synthetic>"
                    and isinstance(message.get("id"), str)
                ):
                    usage = message.get("usage")
                    last[message["id"]] = usage if isinstance(usage, dict) else {}
        for usage in last.values():
            for i, key in enumerate(KEYS):
                value = usage.get(key)
                totals[i] += value if isinstance(value, int) else 0
    return tuple(totals)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".claude" / "projects"
    started = time.perf_counter()
    result = analyze(root, Path.home())
    seconds = time.perf_counter() - started
    u = total_usage(result.sessions)
    ours = (u.input, u.output, u.cache_read, u.cache_write)
    theirs = independent_totals(root)
    print(f"files {result.stats.files}, sessions {len(result.sessions)}, {seconds:.1f}s")
    print(f"analyzer    {ours}")
    print(f"independent {theirs}")
    print("MATCH" if ours == theirs else "MISMATCH")
    return 0 if ours == theirs else 1


if __name__ == "__main__":
    raise SystemExit(main())

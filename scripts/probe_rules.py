"""One-off real check that Claude Code loads the concise rule.

Uses a few requests on the signed-in account.

Uses a temp project's `.claude/rules/` (the same loader as the user-level rules folder), so the real user
config is never touched. Run 1 (no rule) and run 2 (rule) answer the same question; output tokens are
printed for information only (one sample is not a measurement). Run 3 asks Claude to quote the rule's
first line.
Usage: uv run python scripts/probe_rules.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ai_usage_kit.rules.texts import CONCISE

QUESTION = "In two or three sentences, what is a Python list comprehension?"
QUOTE = (
    "Quote, exactly, the first line of every rules file loaded into your instructions, one per line, "
    "nothing else."
)


def _ask(claude: str, project: Path, prompt: str) -> dict[str, object]:
    args = [claude, "-p", prompt, "--output-format", "json", "--max-turns", "1", "--no-session-persistence"]
    run = subprocess.run(
        args, cwd=project, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL
    )
    try:
        data: dict[str, object] = json.loads(run.stdout)
    except ValueError:
        return {"result": run.stdout + run.stderr}
    return data


def _output_tokens(data: dict[str, object]) -> object:
    usage = data.get("usage")
    return usage.get("output_tokens") if isinstance(usage, dict) else None


def main() -> int:
    claude = shutil.which("claude")
    if claude is None:
        print("FAIL: claude CLI not found on PATH")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="eai-rules-probe-"))
    plain, ruled = tmp / "plain", tmp / "ruled"
    plain.mkdir()
    (ruled / ".claude" / "rules").mkdir(parents=True)
    (ruled / ".claude" / "rules" / "eai-concise.md").write_text(CONCISE, encoding="utf-8", newline="")
    without_rule = _ask(claude, plain, QUESTION)
    with_rule = _ask(claude, ruled, QUESTION)
    quoted = _ask(claude, ruled, QUOTE)
    loaded = "# Concise replies" in str(quoted.get("result", ""))
    print(("PASS " if loaded else "FAIL ") + "Claude Code loaded the concise rule from the rules folder")
    before, after = _output_tokens(without_rule), _output_tokens(with_rule)
    print(f"output tokens without rule: {before}; with rule: {after} (one sample; information only)")
    print("without rule:\n" + str(without_rule.get("result", "")).strip())
    print("with rule:\n" + str(with_rule.get("result", "")).strip())
    print(f"temp dir (delete when done): {tmp}")
    return 0 if loaded else 1


if __name__ == "__main__":
    raise SystemExit(main())

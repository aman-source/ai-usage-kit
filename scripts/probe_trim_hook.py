"""One-off real check of the trim hook against Claude Code. Uses a few requests on the signed-in account.

Builds a temp project (300 passing tests, 1 failing test) and a temp settings file with: recorder hooks for
PostToolUse and PostToolUseFailure, and the trim hook (this repo's Python, EAI_STATE_DIR in the temp dir).
Runs `claude -p` there with `--settings <temp file>`. Never writes the real ~/.claude settings.
Prints PASS/FAIL for: failing command never reaches PostToolUse; passing run was trimmed;
Claude saw the trim line.
Usage: uv run python scripts/probe_trim_hook.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ai_usage_kit.trim.install import hook_command_for

RECORDER = """import json, sys, pathlib
data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
line = {"event": data.get("hook_event_name"), "command": (data.get("tool_input") or {}).get("command")}
with open(pathlib.Path(sys.argv[1]), "a", encoding="utf-8") as f:
    f.write(json.dumps(line) + "\\n")
"""


def main() -> int:
    claude = shutil.which("claude")
    if claude is None:
        print("FAIL: claude CLI not found on PATH")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="eai-trim-probe-"))
    project, state, events = tmp / "project", tmp / "state", tmp / "events.jsonl"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_pass.py").write_text(
        "".join(f"def test_ok_{i}():\n    assert True\n\n" for i in range(300)), encoding="utf-8"
    )
    (project / "tests" / "test_fail.py").write_text("def test_bad():\n    assert 1 == 2\n", encoding="utf-8")
    (tmp / "recorder.py").write_text(RECORDER, encoding="utf-8")
    py = Path(sys.executable).as_posix()
    record = f'"{py}" "{(tmp / "recorder.py").as_posix()}" "{events.as_posix()}"'
    eai = shutil.which("eai")
    if eai is None:
        print("FAIL: eai not found; run this with: uv run python scripts/probe_trim_hook.py")
        return 1
    # The shipped form: the quoted executable path from hook_command_for, with the state dir redirected.
    trim = f'EAI_STATE_DIR="{state.as_posix()}" {hook_command_for(eai)}'
    settings = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": record, "timeout": 10}]},
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "if": "Bash(pytest*)", "command": trim, "timeout": 10}],
                },
            ],
            "PostToolUseFailure": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": record, "timeout": 10}]}
            ],
        }
    }
    (tmp / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    prompt = (
        "Use the Bash tool to run exactly these two commands, one at a time, and nothing else: "
        "first `pytest -v tests/test_pass.py`, then `pytest -v tests/test_fail.py`. "
        "Then reply with the last two lines of the first command's output exactly as you saw them."
    )
    args = [
        claude,
        "-p",
        prompt,
        "--settings",
        str(tmp / "settings.json"),
        "--output-format",
        "json",
        "--allowedTools",
        "Bash(pytest *)",
        "--max-turns",
        "6",
    ]
    help_text = subprocess.run([claude, "--help"], capture_output=True, text=True).stdout
    if "--no-session-persistence" in help_text:
        args.append("--no-session-persistence")
    run = subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, timeout=600)
    reply = run.stdout
    lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
    rows = [json.loads(line) for line in lines]
    passing = [r["event"] for r in rows if "test_pass" in (r.get("command") or "")]
    failing = [r["event"] for r in rows if "test_fail" in (r.get("command") or "")]
    saved = list((state / "trim").rglob("*.log")) if (state / "trim").exists() else []
    checks = {
        "failing command reported only as PostToolUseFailure": failing == ["PostToolUseFailure"],
        "passing command reported as PostToolUse": "PostToolUse" in passing,
        "trim saved the full output of the passing run": len(saved) == 1,
        "Claude saw the trimmed text": "eai trim:" in reply or "Pytest: 300 passed" in reply,
    }
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"events: {rows}")
    print(f"temp dir (delete when done): {tmp}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

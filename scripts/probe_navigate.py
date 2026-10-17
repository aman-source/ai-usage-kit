"""One-off real check that Claude Code uses `eai navigate` when the rule and allow rules are in place.

Uses a few requests on the signed-in account. Run only when approved.

Needs the current eai installed as a tool (`uv tool install --force .`) so `eai navigate` is on PATH.
Builds a temp git repo from the navigate test fixture, with a project rule file
`.claude/rules/eai-navigate.md` and project `.claude/settings.local.json` allow rules (the real user settings
are never touched), and graphs in a temp EAI_STATE_DIR. Asks one question with `claude -p` and checks:
1. Claude ran an `eai navigate` command through Bash or PowerShell, and it was not denied;
2. no Read of a whole Python file (every Read of a .py file used offset or limit);
3. the answer names core.py:12 (where `price` is defined).
Usage: uv run python scripts/probe_navigate.py [extra claude args, e.g. --model sonnet]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ai_usage_kit.defaults.items import NAVIGATE_ALLOW_RULES
from ai_usage_kit.rules.texts import NAVIGATE

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "navigate" / "fixtures" / "repos" / "sharedlib"
QUESTION = "In this repo, where is the function `price` defined, and which places call it? Give file:line."
GIT = ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid"]


def _env(state: Path) -> dict[str, str]:
    env = {**os.environ, "EAI_STATE_DIR": str(state)}
    local_bin = Path.home() / ".local" / "bin"
    env["PATH"] = f"{local_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def main() -> int:
    claude = shutil.which("claude")
    if claude is None:
        print("FAIL: claude CLI not found on PATH")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="eai-navigate-probe-"))
    state = tmp / "state"
    env = _env(state)
    eai = shutil.which("eai", path=env["PATH"])
    check = subprocess.run([eai or "eai", "navigate", "--help"], capture_output=True, text=True, env=env)
    if eai is None or check.returncode != 0:
        print("FAIL: an eai with `navigate` is not on PATH; run: uv tool install --force .")
        return 1
    project = tmp / "shop"
    shutil.copytree(FIXTURE, project)
    (project / ".claude" / "rules").mkdir(parents=True)
    (project / ".claude" / "rules" / "eai-navigate.md").write_text(NAVIGATE, encoding="utf-8", newline="")
    settings = {"permissions": {"allow": [*NAVIGATE_ALLOW_RULES, "Read"]}}
    (project / ".claude" / "settings.local.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    subprocess.run([*GIT, "-C", str(project), "init", "-q"], check=True)
    subprocess.run([*GIT, "-C", str(project), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(project), "commit", "-q", "-m", "fixture"], check=True)

    args = [
        claude,
        "-p",
        QUESTION,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "6",
        "--no-session-persistence",
        *sys.argv[1:],
    ]
    run = subprocess.run(
        args,
        cwd=project,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
    )
    events = []
    for line in run.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    uses: dict[str, dict[str, object]] = {}
    results: dict[str, dict[str, object]] = {}
    answer = ""
    for event in events:
        message = event.get("message") if isinstance(event, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                uses[block["id"]] = block
            elif block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = block
        if event.get("type") == "result":
            answer = str(event.get("result", ""))
    navigate_calls = [
        u
        for u in uses.values()
        if u.get("name") in ("Bash", "PowerShell")
        and "eai navigate" in str(u.get("input", {}).get("command", ""))
    ]
    ran = [u for u in navigate_calls if not results.get(str(u["id"]), {}).get("is_error")]
    whole_reads = [
        u
        for u in uses.values()
        if u.get("name") == "Read"
        and str(u.get("input", {}).get("file_path", "")).endswith(".py")
        and not ({"offset", "limit"} & set(u.get("input", {})))
    ]
    checks = [
        (bool(ran), f"Claude ran eai navigate ({len(navigate_calls)} call(s), {len(ran)} not denied)"),
        (not whole_reads, f"no whole-file Read of Python files ({len(whole_reads)} found)"),
        ("core.py:12" in answer, "the answer names core.py:12"),
    ]
    for ok, text in checks:
        print(("PASS " if ok else "FAIL ") + text)
    for u in navigate_calls:
        print("  command:", u.get("input", {}).get("command"))
    print("answer:\n" + answer.strip())
    print(f"temp dir (delete when done): {tmp}")
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

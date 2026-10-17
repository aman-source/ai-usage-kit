"""Scale and accuracy check of navigate on a large real code base. Local only; no Claude usage.

Copies FOLDER to a temp git repo, builds the graph, times queries in fresh processes (load included), and
computes two proxies on 50 sampled definitions (seed 7):
- precision: every `precise` use is on a line that contains the name (or a local import alias of it): 100%;
- recall: plain calls `name(` found by Python's tokenizer (so not in strings or comments) to module-level
  functions with a unique, non-builtin name, that who-uses returns: >= 90%.
Exit 1 when a threshold fails. Usage: uv run python scripts/check_navigate.py FOLDER
"""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
import tracemalloc
from pathlib import Path

from ai_usage_kit.navigate import build, files, snapshot, store
from ai_usage_kit.navigate.graph.config import Config, RepoConfig
from ai_usage_kit.navigate.graph.engine.index import Candidate, Index
from ai_usage_kit.navigate.graph.model import Def

SAMPLE = 50
WHERE_LIMIT = 1.5
WHO_USES_LIMIT = 4.0
RECALL_MIN = 0.90
GIT = ["git", "-c", "user.name=check", "-c", "user.email=check@example.invalid", "-c", "core.autocrlf=false"]


def main(source: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="eai-nav-check-") as tmp:
        repo = Path(tmp) / source.name
        state = Path(tmp) / "state"
        shutil.copytree(
            source, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "site-packages")
        )
        subprocess.run([*GIT, "-C", str(repo), "init", "-q"], check=True)
        subprocess.run([*GIT, "-C", str(repo), "add", "-A"], check=True)
        subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "snapshot"], check=True)
        env = {**os.environ, "EAI_STATE_DIR": str(state)}

        started = time.monotonic()
        status = build.update(repo, env, Path(tmp))
        build_seconds = time.monotonic() - started
        gdir = files.git_dir(repo)
        assert gdir is not None
        current = build.current_db(store.graphs_root(env, Path(tmp)) / store.repo_id(gdir))
        assert status == "built" and current is not None, status
        graph = snapshot.open_graph(current)
        assert graph is not None
        meta = graph.meta
        cfg = Config(repos=[RepoConfig(repo.name, repo)])
        idx = graph.index(cfg)

        paths = files.python_files(repo) or []
        tracemalloc.start()
        Index(cfg).build(files={repo.name: paths})
        peak_mb = tracemalloc.get_traced_memory()[1] / 1e6
        tracemalloc.stop()

        sample = _sample(idx)
        where_s = _time_cli(["where", sample[0].qname], repo, env)
        who_s = _time_cli(["who-uses", sample[0].qname], repo, env)
        precision_ok, precision_bad = _precision(idx, sample)
        expected, found = _recall(idx, repo, paths)
        recall = found / expected if expected else 1.0

        lines = _count_lines(paths)
        print(f"code base: {source} ({len(paths)} Python files, {lines:,} lines)")
        print(
            f"build: {build_seconds:.1f} s, peak traced memory {peak_mb:.0f} MB, graph file "
            f"{current.stat().st_size / 1e6:.1f} MB, indexed {meta['indexed']}, skipped {meta['skipped']}"
        )
        print(f"query (fresh process, load included): where {where_s:.2f} s, who-uses {who_s:.2f} s")
        print(
            f"precision proxy: {precision_ok}/{precision_ok + len(precision_bad)} precise uses on a line "
            "naming the target"
        )
        for bad in precision_bad[:10]:
            print(f"  not naming it: {bad}")
        print(f"recall proxy: {found}/{expected} plain calls found ({recall:.1%})")
        graph.close()
        failed = []
        if precision_bad:
            failed.append("precision < 100%")
        if recall < RECALL_MIN:
            failed.append(f"recall < {RECALL_MIN:.0%}")
        if where_s > WHERE_LIMIT:
            failed.append(f"where > {WHERE_LIMIT} s")
        if who_s > WHO_USES_LIMIT:
            failed.append(f"who-uses > {WHO_USES_LIMIT} s")
        print("RESULT: " + ("FAIL (" + ", ".join(failed) + ")" if failed else "PASS"))
        return 1 if failed else 0


def _sample(idx: Index) -> list[Def]:
    unique = [ds[0] for name, ds in idx.by_short.items() if len(ds) == 1 and not name.startswith("__")]
    pool = sorted((d for d in unique if d.kind in ("function", "method")), key=lambda d: d.qname)
    return random.Random(7).sample(pool, min(SAMPLE, len(pool)))


def _time_cli(args: list[str], repo: Path, env: dict[str, str]) -> float:
    started = time.monotonic()
    subprocess.run(
        [sys.executable, "-m", "ai_usage_kit", "navigate", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        check=True,
    )
    return time.monotonic() - started


def _precision(idx: Index, sample: list[Def]) -> tuple[int, list[str]]:
    ok, bad = 0, []
    for d in sample:
        for u in idx.uses(Candidate("symbol", d.qname, d=d)):
            if u.conf != "precise":
                continue
            fi = idx.files.get((u.repo, u.path))
            aliases = [local for local, target in (fi.aliases.items() if fi else []) if target == d.qname]
            if (
                d.name in u.snippet
                or any(a in u.snippet for a in aliases)
                or _continued(idx, u.repo, u.path, u.line, d.name)
            ):
                ok += 1
            else:
                bad.append(f"{d.qname} at {u.path}:{u.line}: {u.snippet}")
    return ok, bad


def _continued(idx: Index, repo: str, path: str, line: int, name: str) -> bool:
    """A call whose name is on the next lines of a multi-line expression starting at `line`."""
    text = idx.lines(repo, path)
    return any(name in t for t in text[line : line + 3])


def _recall(idx: Index, repo: Path, paths: list[Path]) -> tuple[int, int]:
    names = {
        ds[0].name: ds[0]
        for name, ds in idx.by_short.items()
        if len(ds) == 1
        and ds[0].kind == "function"
        and ds[0].qname.count(".") == ds[0].qname.count(".")
        and not hasattr(builtins, name)
        and not name.startswith("_")
        and _module_level(idx, ds[0])
    }
    chosen = dict(random.Random(7).sample(sorted(names.items()), min(SAMPLE, len(names))))
    expected: set[tuple[str, int, str]] = set()
    for path in paths:
        rel = path.relative_to(repo).as_posix()
        if (repo.name, rel) not in idx.files:
            continue
        for line, name in _plain_calls(path, set(chosen)):
            expected.add((rel, line, name))
    found = 0
    for name, d in chosen.items():
        hits = {(u.path, u.line) for u in idx.uses(Candidate("symbol", d.qname, d=d)) if u.kind == "call"}
        found += sum(1 for rel, line, n in expected if n == name and (rel, line) in hits)
    return len(expected), found


def _module_level(idx: Index, d: Def) -> bool:
    fi = idx.files.get((d.repo, d.path))
    return fi is not None and d.qname == f"{fi.module}.{d.name}"


def _plain_calls(path: Path, names: set[str]) -> list[tuple[int, str]]:
    try:
        source = path.read_bytes()
        tokens = list(tokenize.tokenize(io.BytesIO(source).readline))
    except (OSError, SyntaxError, tokenize.TokenError, UnicodeDecodeError):
        return []
    out = []
    for prev, tok, nxt in zip(tokens, tokens[1:], tokens[2:], strict=False):
        if tok.type == tokenize.NAME and tok.string in names and nxt.string == "(":
            if prev.string in (".", "def", "class"):
                continue
            out.append((tok.start[0], tok.string))
    return out


def _count_lines(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        with contextlib.suppress(OSError):
            total += path.read_bytes().count(b"\n")
    return total


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print("usage: uv run python scripts/check_navigate.py FOLDER", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]).resolve()))

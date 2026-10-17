"""The four navigate answers: where, who_uses, impact, outline. Each answers in one call as plain text.

Output contract: exact lines, confidence per result, ambiguity listed with qualified names, pages of 40,
several names per call (`a|b`), name patterns (`*price*`), callers of callers (`depth`), plain `path:line`
locations for a single repo, a staleness line, and a size cap.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import TypeVar

from ai_usage_kit.navigate.graph.config import Config
from ai_usage_kit.navigate.graph.diffparse import parse_diff
from ai_usage_kit.navigate.graph.engine.index import Candidate, Index, Resolution
from ai_usage_kit.navigate.graph.model import Def, Use

MAX_CHARS = 20_000
MAX_NAMES = 10
MAX_CHANGED = 20
WHERE_LIMIT = 40
CANDIDATE_LIMIT = 8
# File systems that ignore letter case by default (Windows, macOS): match typed absolute paths that way too.
CASE_INSENSITIVE_PATHS = sys.platform in ("win32", "darwin")
KINDS = ("call", "import", "subclass", "decorator", "type", "ref", "http")
EXPANDABLE = ("function", "method", "class")


class ToolError(Exception):
    """Wrong arguments; the message is shown to the caller."""


@dataclass(frozen=True)
class Freshness:
    indexed: str | None
    stale: bool
    changed: list[str] = field(default_factory=list)


def split_targets(text: str) -> list[str]:
    """`a|b| a ||c` -> [a, b, c]: like grep alternation, duplicates and empty parts dropped."""
    seen: list[str] = []
    for part in (text or "").split("|"):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def is_pattern(target: str) -> bool:
    return "*" in target or "?" in target


def expand_pattern(idx: Index, pattern: str) -> list[Def]:
    """Definitions whose qualified or short name matches the pattern; non-module definitions first."""
    matching = getattr(idx.defs, "matching", None)  # an opened graph filters in SQL first
    if matching is not None:
        found: list[Def] = matching(pattern)
    else:
        found = [
            d for d in idx.defs.values() if fnmatchcase(d.qname, pattern) or fnmatchcase(d.name, pattern)
        ]
    chosen = [d for d in found if d.kind != "module"] or found
    return sorted(chosen, key=lambda d: (d.is_test, d.repo, d.path, d.line))


_T = TypeVar("_T")


def _page(items: list[_T], page: int, size: int) -> tuple[list[_T], int]:
    lo = (page - 1) * size
    return items[lo : lo + size], max(0, len(items) - lo - size)


_Entry = tuple[int, int, Use, str]  # level, candidate index, use, via (short name of the caller expanded)


class Tools:
    def __init__(
        self,
        index: Index,
        cfg: Config,
        freshness: Callable[[], Freshness] | None = None,
        show_repo: bool = False,
    ) -> None:
        self.idx = index
        self.cfg = cfg
        self.freshness = freshness
        self.show_repo = show_repo
        self.size = cfg.page_size

    # ------------------------------------------------------------------ public answers
    def where(self, symbol: str) -> str:
        return self._answer("where", symbol, self._where_one)

    def who_uses(self, symbol: str, kinds: list[str] | None = None, page: int = 1, depth: int = 1) -> str:
        _check_page(page)
        if depth not in (1, 2, 3):
            raise ToolError("depth must be 1, 2 or 3")
        bad = [k for k in kinds or [] if k not in KINDS]
        if bad:
            raise ToolError(f"unknown kind {', '.join(bad)}; use: {', '.join(KINDS)}")
        kset = set(kinds) if kinds else None
        title = "who_uses" + (f" (depth {depth})" if depth > 1 else "")
        return self._answer(title, symbol, lambda t: self._who_uses_one(t, kset, page, depth))

    def impact(self, symbol: str | None = None, diff: str | None = None, page: int = 1) -> str:
        _check_page(page)
        if bool(symbol and symbol.strip()) == bool(diff):
            raise ToolError("give a symbol or a diff (exactly one)")
        if diff:
            cands, notes = self._diff_candidates(diff)
            lines = ["impact of diff", *self._fresh_lines(), *self._impact_body(cands, notes, page, True)]
            return _cap("\n".join(lines))
        return self._answer("impact", symbol or "", lambda t: self._impact_symbol(t, page))

    def outline(self, path: str) -> str:
        rel = self._relative(path)
        head = [f"outline {rel}", *self._fresh_lines()]
        key = self._file_key(rel)
        if key is None:
            note = f"not indexed: {rel} (not a Python file in this repo, or new since the last build)"
            return _cap("\n".join([*head, note]))
        module = self.idx.files[key].module
        lines = list(head)
        for d in sorted(self.idx.defs_in_file[key], key=lambda d: (d.start, d.qname)):
            if d.kind == "module":
                continue
            depth = d.qname[len(module) + 1 :].count(".")
            lines.append(f"{'  ' * depth}{d.start}-{d.end}  {d.kind}  {d.name}")
        if len(lines) == len(head):
            lines.append("no definitions in this file")
        return _cap("\n".join(lines))

    # ------------------------------------------------------------------ several names
    def _answer(self, title: str, symbol: str, one: Callable[[str], list[str]]) -> str:
        targets = split_targets(symbol)
        if not targets:
            raise ToolError(
                "a symbol is required (name, module.name, Class.method, a|b, or a pattern like *x*)"
            )
        rest = targets[MAX_NAMES:]
        targets = targets[:MAX_NAMES]
        lines = [f"{title} {'|'.join(targets)}", *self._fresh_lines()]
        if len(targets) == 1:
            lines += one(targets[0])
        else:
            for target in targets:
                lines += ["", f"== {target}", *one(target)]
        if rest:
            lines.append(f"... {len(rest)} more names not answered; ask again with the rest")
        return _cap("\n".join(lines))

    def _resolve(self, target: str, limit: int) -> Resolution:
        if is_pattern(target):
            defs = expand_pattern(self.idx, target)
            return Resolution([Candidate("symbol", d.qname, d=d) for d in defs[:limit]], total=len(defs))
        return self.idx.resolve_target(target)

    # ------------------------------------------------------------------ where
    def _where_one(self, target: str) -> list[str]:
        res = self._resolve(target, WHERE_LIMIT)
        if not res.candidates:
            return self._not_found(target)
        lines: list[str] = []
        if res.total > 1:
            lines.append(f"{res.total} definitions match; qualify the name to narrow it")
        for c in res.candidates:
            if c.route is not None:
                r = c.route
                lines.append(f"{self._loc(r.repo, r.path, r.line)}  route  {c.qname} -> {r.handler or '?'}")
                continue
            d = c.d
            assert d is not None
            moved = f"  (def at line {d.line})" if d.line != d.start else ""
            lines.append(f"{self._loc(d.repo, d.path, d.start)}-{d.end}  {d.kind}  {d.qname}{moved}")
        if res.total > len(res.candidates):
            lines.append(f"... {res.total - len(res.candidates)} more not shown")
        lines.append("read only these lines (Read with offset/limit)")
        return lines

    # ------------------------------------------------------------------ who_uses
    def _who_uses_one(self, target: str, kset: set[str] | None, page: int, depth: int) -> list[str]:
        res = self._resolve(target, CANDIDATE_LIMIT)
        if not res.candidates:
            return self._not_found(target)
        entries: list[_Entry] = []
        per_cand: dict[int, int] = {}
        seen = {(c.d.repo, c.d.qname) for c in res.candidates if c.d is not None}
        for i, c in enumerate(res.candidates):
            uses = self.idx.uses(c, kset)
            per_cand[i] = len(uses)
            for u in uses:
                entries.append((0, i, u, ""))
                if depth > 1:
                    self._expand(u, 1, depth, kset, seen, entries, i)
        shown, more = _page(entries, page, self.size)
        lines = self._ambiguity(res, target)
        by_cand: dict[int, list[_Entry]] = defaultdict(list)
        for entry in shown:
            by_cand[entry[1]].append(entry)
        for i, c in enumerate(res.candidates):
            mine = by_cand.get(i, [])
            if not mine and not (page == 1 and per_cand[i] == 0):
                continue
            n = per_cand[i]
            lines += [
                "",
                f"== {c.qname}: {n} use{'s' if n != 1 else ''} ({self._cand_desc(c).split('  ', 1)[1]})",
            ]
            if n == 0:
                hint = (
                    " (names that only match dynamically are not indexed; try Grep)"
                    if c.kind == "symbol"
                    else ""
                )
                lines.append("   no uses found" + hint)
            if depth > 1:
                for level, _, u, via in mine:
                    note = f"  via {via}" if level else ""
                    lines.append(
                        f"  {'  ' * level}{self._loc(u.repo, u.path, u.line)}  {u.conf} {u.kind}  "
                        f"{u.snippet}{note}"
                    )
                continue
            groups: dict[str, list[Use]] = defaultdict(list)
            for _, _, u, _ in mine:
                groups[u.repo].append(u)
            for repo in sorted(groups):
                if self.show_repo:
                    lines.append(f"{repo} ({len(groups[repo])})")
                for u in groups[repo]:
                    lines.append(f"  {self._loc(u.repo, u.path, u.line)}  {u.conf} {u.kind}  {u.snippet}")
        if more:
            lines.append(f"... {more} more (add --page {page + 1})")
        return lines

    def _expand(
        self,
        use: Use,
        level: int,
        depth: int,
        kset: set[str] | None,
        seen: set[tuple[str, str]],
        entries: list[_Entry],
        cand: int,
    ) -> None:
        """Add the uses of the definition that contains `use` (its caller), once per definition."""
        d = self.idx.defs.get((use.repo, use.scope))
        if d is None or d.kind not in EXPANDABLE or (d.repo, d.qname) in seen:
            return
        seen.add((d.repo, d.qname))
        for u in self.idx.uses(Candidate("symbol", d.qname, d=d), kset):
            entries.append((level, cand, u, d.name))
            if level + 1 < depth:
                self._expand(u, level + 1, depth, kset, seen, entries, cand)

    # ------------------------------------------------------------------ impact
    def _impact_symbol(self, target: str, page: int) -> list[str]:
        res = self._resolve(target, CANDIDATE_LIMIT)
        if not res.candidates:
            return self._not_found(target)
        notes = self._ambiguity(res, target) if len(res.candidates) > 1 else []
        return self._impact_body(res.candidates, notes, page, False)

    def _impact_body(self, cands: list[Candidate], notes: list[str], page: int, from_diff: bool) -> list[str]:
        all_uses: dict[tuple[str, str, int, str], tuple[Use, str]] = {}
        for c in cands:
            for u in self.idx.uses(c):
                d = c.d
                if (
                    from_diff
                    and d is not None
                    and (u.repo, u.path) == (d.repo, d.path)
                    and d.start <= u.line <= d.end
                ):
                    continue  # inside the changed symbol itself
                all_uses.setdefault((u.repo, u.path, u.line, u.kind), (u, c.qname))
        uses = sorted((u for u, _ in all_uses.values()), key=lambda u: (u.repo, u.path, u.line))
        via = {key: q for key, (_, q) in all_uses.items()}
        multi = len(cands) > 1
        files: dict[tuple[str, str], list[Use]] = defaultdict(list)
        for u in uses:
            files[(u.repo, u.path)].append(u)
        tests = self.idx.tests_for(uses, [c.d for c in cands if c.d])
        lines: list[str] = []
        if cands:
            more_c = f"; ... +{len(cands) - 6}" if len(cands) > 6 else ""
            lines.append("changed: " + "; ".join(self._cand_desc(c) for c in cands[:6]) + more_c)
        lines += notes
        by_repo: dict[str, int] = defaultdict(int)
        for (r, _p), us in files.items():
            by_repo[r] += len(us)
        nf, nu = len(files), len(uses)
        counts = f"{nf} file{'s' if nf != 1 else ''}, {nu} use{'s' if nu != 1 else ''}"
        if self.show_repo:
            repos = f"{len(by_repo)} repo{'s' if len(by_repo) != 1 else ''}, "
            spread = " - " + ", ".join(f"{r} ({n})" for r, n in sorted(by_repo.items())) if by_repo else ""
            lines.append(f"affected: {repos}{counts}{spread}")
        else:
            lines.append(f"affected: {counts}")
        shown, more = _page(sorted(files.items()), page, self.size)
        current = None
        for (r, p), us in shown:
            if self.show_repo and r != current:
                lines.append(f"{r}:")
                current = r
            parts = []
            for u in us[:8]:
                text = f"L{u.line} {u.kind}/{u.conf}"
                if multi:
                    text += f" via {via[(u.repo, u.path, u.line, u.kind)].rsplit('.', 1)[-1]}"
                parts.append(text)
            if len(us) > 8:
                parts.append(f"+{len(us) - 8} more")
            lines.append(f"  {p} - " + ", ".join(parts))
        if more:
            lines.append(f"... {more} more files (add --page {page + 1})")
        if tests:
            lines.append(f"tests to run ({len(tests)}):")
            for h in tests[: self.size]:
                lines.append(f"  {self._file(h.repo, h.path)} - {h.reason}")
            if len(tests) > self.size:
                lines.append(f"  ... {len(tests) - self.size} more")
        else:
            lines.append("tests to run: none found through direct use or imports")
        if not uses:
            lines.append("no uses found - nothing else in the index references it")
        return lines

    def _diff_candidates(self, diff: str) -> tuple[list[Candidate], list[str]]:
        changes = parse_diff(diff)
        if not changes:
            raise ToolError("could not parse the diff (expected a unified diff, e.g. from `git diff`)")
        repo = self.idx.repo_of_paths([c.path for c in changes])
        if repo is None:
            raise ToolError("no changed file in the diff is in the index")
        notes: list[str] = []
        cands: list[Candidate] = []
        seen: set[tuple[str, str]] = set()

        def add(d: Def) -> None:
            if (d.repo, d.qname) not in seen:
                seen.add((d.repo, d.qname))
                cands.append(Candidate("symbol", d.qname, d=d))

        unindexed = []
        for ch in changes:
            key = (repo, ch.path)
            if key not in self.idx.files:
                if not ch.new_file:
                    unindexed.append(ch.path)
                continue
            if ch.new_file:
                continue
            if ch.deleted:
                fi = self.idx.files[key]
                depth = fi.module.count(".") + 1
                for d in self.idx.defs_in_file[key]:
                    if d.kind != "module" and d.qname.count(".") == depth:
                        add(d)
                continue
            lines = [ln for ln, text in ch.removed if text.strip() and not text.strip().startswith("#")]
            new_top_pos = None  # inside an inserted block that started a new top-level construct
            for pos, text in ch.inserted:
                if new_top_pos != pos:
                    new_top_pos = None
                if not text.strip():
                    continue
                if text[0] not in " \t":
                    new_top_pos = pos
                elif new_top_pos is None:  # indented insert: belongs to the def around it
                    lines.append(pos - 1 if self.idx.symbols_in(repo, ch.path, [pos - 1]) else pos)
            defs = self.idx.symbols_in(repo, ch.path, lines)
            covered = {ln for d in defs for ln in range(d.start, d.end + 1)}
            for d in defs:
                add(d)
            if any(ln not in covered and ln > 0 for ln in lines):
                md = self.idx.module_def(repo, ch.path)
                if md:
                    add(md)
        if unindexed:
            notes.append("not indexed (not Python, or new to the index): " + ", ".join(unindexed[:6]))
        if not cands:
            notes.append("no existing symbol changed (only additions or files not in the index)")
        if self.show_repo:
            notes.insert(0, f"repo: {repo}")
        return cands, notes

    # ------------------------------------------------------------------ shared pieces
    def _fresh_lines(self) -> list[str]:
        if self.freshness is None:
            return []
        fresh = self.freshness()
        if not fresh.stale:
            return []
        built = fresh.indexed or "unknown"
        if not fresh.changed:
            return [f"stale: true (graph built at {built}); the repo moved since; results may be off"]
        shown = ", ".join(fresh.changed[:MAX_CHANGED])
        extra = len(fresh.changed) - MAX_CHANGED
        more = f", ... {extra} more" if extra > 0 else ""
        return [f"stale: true (graph built at {built}); changed since: {shown}{more}; read those directly"]

    def _not_found(self, target: str) -> list[str]:
        hint = self._suggest(target)
        return [f"not found: {target!r}", *([hint] if hint else []), "try Grep for the text"]

    def _suggest(self, target: str) -> str:
        names = list(self.idx.by_short.keys())
        short = target.rsplit(".", 1)[-1].replace("*", "").replace("?", "")
        close = difflib.get_close_matches(short, names, n=5, cutoff=0.6)
        if not close:
            return ""
        qualified = sorted({d.qname for n in close for d in self.idx.by_short[n]})[:6]
        return "did you mean: " + ", ".join(qualified)

    def _ambiguity(self, res: Resolution, target: str) -> list[str]:
        if len(res.candidates) <= 1:
            return []
        out = [f"ambiguous: '{target}' matches {res.total} symbols - results are shown per symbol:"]
        out += [f"  {i}. {self._cand_desc(c)}" for i, c in enumerate(res.candidates, 1)]
        if res.total > len(res.candidates):
            hidden = res.total - len(res.candidates)
            out.append(f"  ... {hidden} more not shown; qualify the name (module.Class.method)")
        return out

    def _cand_desc(self, c: Candidate) -> str:
        if c.route is not None:
            r = c.route
            return f"{c.qname}  route -> {r.handler or '?'}  {self._loc(r.repo, r.path, r.line)}"
        d = c.d
        assert d is not None
        return f"{c.qname}  {d.kind}  {self._loc(d.repo, d.path, d.line)}"

    def _loc(self, repo: str, path: str, line: int) -> str:
        return f"{self._file(repo, path)}:{line}"

    def _file(self, repo: str, path: str) -> str:
        return f"{repo}:{path}" if self.show_repo else path

    def _relative(self, path: str) -> str:
        text = (path or "").strip().replace("\\", "/")
        if os.path.isabs(text) or re.match(r"^[A-Za-z]:/", text):
            original = os.path.abspath(text)
            folded = _fold(original)
            for rc in self.cfg.repos:
                root = _fold(os.path.abspath(rc.path))
                if folded.startswith(root + os.sep):
                    return original[len(root) + 1 :].replace("\\", "/")
            return text
        while text.startswith("./"):
            text = text[2:]
        return text

    def _file_key(self, rel: str) -> tuple[str, str] | None:
        for rc in self.cfg.repos:
            if (rc.name, rel) in self.idx.files:
                return rc.name, rel
        folded = rel.lower()  # a path typed with other letter case (Windows, macOS)
        matches = [key for key in self.idx.files if key[1].lower() == folded]
        return matches[0] if len(matches) == 1 else None


def _fold(path: str) -> str:
    folded = os.path.normcase(path)
    return folded.lower() if CASE_INSENSITIVE_PATHS else folded


def _check_page(page: int) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ToolError("page must be a whole number from 1")


def _cap(text: str) -> str:
    limit = MAX_CHARS
    if len(text) <= limit:
        return text
    note = f"... output cut at {limit} characters; add --page 2 or ask for a narrower symbol"
    budget = max(0, limit - len(note) - 1)
    cut = text.rfind("\n", 0, budget)
    return text[: cut if cut > 0 else budget] + "\n" + note

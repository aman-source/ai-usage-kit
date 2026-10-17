"""The code graph: build, finalize (cross-file resolution) and query.

Engine contract (what `navigate.tools` relies on) — a second engine (SCIP, codebase-memory-mcp) only
needs to provide these members:

    commits / dirty            repo -> sha at index time
    resolve_target(text)       -> Resolution
    uses(candidate, kinds)     -> list[Use]
    symbols_in(repo, path, lines) -> list[Def]
    tests_for(uses, defs)      -> list[TestHit]
    lines(repo, path)          -> list[str]
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path
from dataclasses import dataclass

from ..config import Config, head_commit, is_dirty, iter_files
from ..linker import PackageMap, _seg_match, join_prefix, link_routes, route_segments
from ..model import HTTP, INFERRED, PRECISE, Def, HttpCall, Ref, Route, Use
from .collect import FileInfo, Globals, Include, module_of, scan_file, visit_file

KIND_ORDER = {"call": 0, "subclass": 1, "import": 2, "decorator": 3, "type": 4, "ref": 5, "http": 6}
ROUTE_Q = re.compile(r"^(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?(/\S*)$", re.I)


@dataclass
class Candidate:
    kind: str  # symbol | route
    qname: str  # display name
    d: Def | None = None
    route: Route | None = None
    route_idx: int = -1


@dataclass
class Resolution:
    candidates: list[Candidate]
    total: int = 0  # matches before the cap
    exact: bool = False


@dataclass
class TestHit:
    repo: str
    path: str
    reason: str
    direct: bool


class Index:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pm = PackageMap()
        self.files: dict[tuple[str, str], FileInfo] = {}
        self.defs: dict[tuple[str, str], Def] = {}
        self.by_short: dict[str, list[Def]] = defaultdict(list)
        self.defs_in_file: dict[tuple[str, str], list[Def]] = defaultdict(list)
        self.reexports: dict[tuple[str, str], str] = {}
        self.g = Globals()
        self.by_prefix: dict[tuple[str, str], list[Ref]] = defaultdict(list)
        self.by_attr: dict[str, list[Ref]] = defaultdict(list)
        self.routes: list[Route] = []
        self.http_calls: list[HttpCall] = []
        self.route_calls: dict[int, list[HttpCall]] = {}
        self.handler_routes: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.importers: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        self.repo_deps: dict[tuple[str, str], int] = defaultdict(int)
        self.commits: dict[str, str | None] = {}
        self.dirty: dict[str, bool] = {}
        self._lines: dict[tuple[str, str], list[str]] = {}
        self.stats: dict[str, int] = {}

    # ------------------------------------------------------------- building
    def build(self, files: dict[str, list[Path]] | None = None) -> "Index":
        """files: optional explicit file list per repo name (eai uses git's list); else walk the folder."""
        sources: dict[tuple[str, str], str] = {}
        for rc in self.cfg.repos:
            if files is None:  # eai passes files= and asks git itself (read-only, no index refresh)
                self.commits[rc.name] = head_commit(rc.path)
                self.dirty[rc.name] = is_dirty(rc.path) if self.commits[rc.name] else False
            else:
                self.commits[rc.name], self.dirty[rc.name] = None, False
            for top in rc.packages:
                self.pm.add(rc.name, top, explicit=True)
            for f in files[rc.name] if files is not None and rc.name in files else iter_files(rc.path, ".py"):
                rel = f.relative_to(rc.path).as_posix()
                mod, is_init = module_of(rel, rc.roots())
                if not mod:
                    continue
                try:
                    src = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                fi = scan_file(rc.name, rel, src, mod, is_init)
                if fi is None:
                    continue
                self.files[(rc.name, rel)] = fi
                sources[(rc.name, rel)] = src
                self.pm.add(rc.name, mod.split(".")[0], explicit=False)
        # definitions + re-exports + constants
        for (repo, rel), fi in self.files.items():
            for d in fi.defs:
                self.defs.setdefault((repo, d.qname), d)
                self.defs_in_file[(repo, rel)].append(d)
            for name, q in fi.consts.items():
                self.g.consts[f"{fi.module}.{name}"] = q
        for (repo, rel), fi in self.files.items():
            for local, target in fi.aliases.items():
                key = (repo, f"{fi.module}.{local}")
                if key not in self.defs and target != key[1]:
                    self.reexports.setdefault(key, target)
        self._star_reexports()
        for d in self.defs.values():
            self.by_short[d.name].append(d)
        self.g.star_lookup = self._star_lookup
        # references
        for (repo, rel), fi in self.files.items():
            try:
                visit_file(fi, sources[(repo, rel)], self.g)
            except (RecursionError, SyntaxError, ValueError):
                continue
        sources.clear()
        self.finalize()
        return self

    def _star_reexports(self) -> None:
        """`from ._client import *` in httpx/__init__.py makes httpx.Client an alias of httpx._client.Client."""
        kids: dict[tuple[str, str], set[str]] = defaultdict(
            set
        )  # (repo, module) -> names defined directly in it
        for (r, q), d in self.defs.items():
            if d.kind != "module" and "." in q:
                kids[(r, q.rsplit(".", 1)[0])].add(q.rsplit(".", 1)[1])
        by_mod = {(r, fi.module): fi for (r, _p), fi in self.files.items()}

        def exports(repo: str, module: str) -> list[str]:
            fi = by_mod.get((repo, module))
            if fi is not None and fi.all_names is not None:
                return list(fi.all_names)
            return sorted(n for n in kids.get((repo, module), ()) if not n.startswith("_"))

        for _ in range(4):  # chains of star imports settle in a few passes
            changed = False
            for (repo, _p), fi in self.files.items():
                for s in fi.stars:
                    owner = self.pm.locate(repo, s)
                    if owner is None:
                        continue
                    for name in exports(owner, s):
                        key, target = (repo, f"{fi.module}.{name}"), f"{s}.{name}"
                        if key not in self.defs and key not in self.reexports and key[1] != target:
                            self.reexports[key] = target
                            kids[(repo, fi.module)].add(name)  # now exported from here too
                            changed = True
            if not changed:
                break

    def _star_lookup(self, repo: str, fi: FileInfo, name: str) -> str | None:
        for s in fi.stars:
            q = f"{s}.{name}"
            r = self.pm.locate(repo, q)
            if r and ((r, q) in self.defs or (r, q) in self.reexports):
                return q
        return None

    def canonical(self, repo: str, q: str) -> tuple[str | None, str]:
        """Follow re-exports to the defining (repo, qname). Repo None = external package."""
        r: str | None = repo
        for _ in range(8):
            r = self.pm.locate(r or repo, q)
            if r is None:
                return None, q
            parts = q.split(".")
            moved = False
            for i in range(len(parts), 0, -1):
                pre = ".".join(parts[:i])
                if (r, pre) in self.defs:
                    break
                t = self.reexports.get((r, pre))
                if t:
                    q = t + ("." + ".".join(parts[i:]) if i < len(parts) else "")
                    moved = True
                    break
            if not moved:
                return r, q
        return r, q

    def class_def(self, repo: str, q: str) -> tuple[str, Def] | None:
        r, cq = self.canonical(repo, q)
        d = self.defs.get((r, cq)) if r else None
        return (r, d) if d is not None and d.kind == "class" and r else None

    def lookup_member(
        self, repo: str, cls_q: str, name: str, skip_self=False, _seen=None
    ) -> tuple[str, str] | None:
        _seen = _seen if _seen is not None else set()
        if (repo, cls_q) in _seen:
            return None
        _seen.add((repo, cls_q))
        d = self.defs.get((repo, cls_q))
        if d is None or d.kind != "class":
            return None
        if not skip_self and (repo, f"{cls_q}.{name}") in self.defs:
            return repo, f"{cls_q}.{name}"
        for b in d.bases:
            cd = self.class_def(repo, b)
            if cd:
                hit = self.lookup_member(cd[0], cd[1].qname, name, False, _seen)
                if hit:
                    return hit
        return None

    def _type_class(self, repo: str, base: tuple) -> tuple[str, str, bool] | None:
        """-> (repo, class qname, skip_self)"""
        kind, q = base[0], base[1]
        if kind in ("self", "super"):
            d = self.defs.get((repo, q))
            return (repo, q, kind == "super") if d is not None and d.kind == "class" else None
        cd = self.class_def(repo, q)
        return (cd[0], cd[1].qname, False) if cd else None

    def _resolve_typed(self, ref: Ref) -> tuple[str, str] | None:
        tc = self._type_class(ref.repo, ref.base)  # type: ignore[arg-type]
        if tc is None:
            return None
        repo, cq, skip = tc
        for attr in ref.attrs[:-1]:
            t = None
            seen = set()
            todo = [(repo, cq)]
            while todo and t is None:  # attr type through the MRO
                r2, c2 = todo.pop(0)
                if (r2, c2) in seen:
                    continue
                seen.add((r2, c2))
                t = self.g.class_attr_types.get((r2, c2, attr))
                d = self.defs.get((r2, c2))
                if d:
                    for b in d.bases:
                        cd = self.class_def(r2, b)
                        if cd:
                            todo.append((cd[0], cd[1].qname))
            if t is None or t.kind != "t" or t.attrs or not t.base or t.base[0] != "inst":
                return None
            nxt = self._type_class(repo, t.base)
            if nxt is None:
                return None
            repo, cq, skip = nxt
        return self.lookup_member(repo, cq, ref.attrs[-1], skip_self=skip)

    def finalize(self) -> None:
        for ref in self.g.refs:
            if ref.qname is not None:
                ref.raw = ref.qname
                r, q = self.canonical(ref.repo, ref.qname)
                if r is None:
                    continue
                ref.qname = q
                self._register(r, q, ref)
            elif ref.base is not None:
                hit = self._resolve_typed(ref)
                if hit:
                    ref.qname = hit[1]
                    self._register(hit[0], hit[1], ref)
                else:
                    ref.conf = INFERRED
                    self.by_attr[ref.attrs[-1]].append(ref)
            else:
                self.by_attr[ref.attrs[-1]].append(ref)
        self._module_graph()
        self._routes()
        self.stats = {
            "files": len(self.files),
            "defs": len(self.defs),
            "refs": len(self.g.refs),
            "routes": len(self.routes),
            "http_calls": len(self.http_calls),
            "route_links": sum(len(v) for v in self.route_calls.values()),
        }

    def _register(self, repo: str, q: str, ref: Ref) -> None:
        parts = q.split(".")
        for i in range(1, len(parts) + 1):
            self.by_prefix[(repo, ".".join(parts[:i]))].append(ref)
        ref.target_repo = repo

    def _module_graph(self) -> None:
        def module_for(repo: str, q: str) -> tuple[str, str] | None:
            r = self.pm.locate(repo, q)
            if r is None:
                return None
            parts = q.split(".")
            for i in range(len(parts), 0, -1):
                d = self.defs.get((r, ".".join(parts[:i])))
                if d is not None and d.kind == "module":
                    return r, d.qname
            return None

        mod_of_file = {k: (k[0], fi.module) for k, fi in self.files.items()}
        for ref in self.g.refs:
            if ref.kind != "import":
                continue
            src = mod_of_file.get((ref.repo, ref.path))
            if src is None:
                continue
            for q in {ref.raw, ref.qname}:
                if not q:
                    continue
                dst = module_for(ref.repo, q)
                if dst and dst != src:
                    self.importers[dst].add(src)
                    if dst[0] != src[0]:
                        self.repo_deps[(src[0], dst[0])] += 1

    def _routes(self) -> None:
        # canonical router keys
        ctor: dict[tuple[str, str], str] = {}
        for (repo, q), p in self.g.ctor_prefix.items():
            r, cq = self.canonical(repo, q)
            ctor[(r or repo, cq)] = p
        parents: dict[tuple[str, str], list[tuple[tuple[str, str], Include]]] = defaultdict(list)
        for repo, inc in self.g.includes:
            cr, cq = self.canonical(repo, inc.child)
            pk = None
            if inc.parent:
                pr, pq = self.canonical(repo, inc.parent)
                pk = (pr or repo, pq)
            parents[(cr or repo, cq)].append((pk, inc))  # type: ignore[arg-type]

        def prefix_of(key, depth=0) -> list[str]:
            own = ctor.get(key, "")
            ps = parents.get(key)
            if not ps or depth > 6:
                return [own]
            out = []
            for pk, inc in ps[:3]:
                up = prefix_of(pk, depth + 1) if pk else [""]
                for u in up:
                    if inc.override and inc.prefix is not None:
                        out.append(join_prefix(u, inc.prefix))
                    else:
                        out.append(join_prefix(u, inc.prefix or "", own))
            return out

        rcfg = {r.name: r for r in self.cfg.repos}
        for rr in self.g.routes:
            key = None
            if rr.router:
                r, q = self.canonical(rr.repo, rr.router)
                key = (r or rr.repo, q)
            for pre in prefix_of(key) if key else [""]:
                full = join_prefix(rcfg[rr.repo].route_prefix, pre, rr.template) or "/"
                handler = None
                if rr.handler:
                    hr, hq = self.canonical(rr.repo, rr.handler)
                    handler = hq if (hr, hq) in self.defs else rr.handler
                    hrepo = hr or rr.repo
                for m in rr.methods:
                    self.routes.append(
                        Route(
                            rr.repo,
                            rr.path,
                            rr.line,
                            m.upper(),
                            full,
                            route_segments(full),
                            handler,
                            rr.framework,
                        )
                    )
                    if handler:
                        self.handler_routes[(hrepo, handler)].append(len(self.routes) - 1)
        self.http_calls = self.g.calls
        self.route_calls = link_routes(self.routes, self.http_calls)

    # --------------------------------------------------------------- source
    def lines(self, repo: str, path: str) -> list[str]:
        key = (repo, path)
        if key not in self._lines:
            if len(self._lines) > 300:
                self._lines.clear()
            rc = self.cfg.repo(repo)
            try:
                self._lines[key] = (rc.path / path).read_text(encoding="utf-8", errors="replace").splitlines()  # type: ignore[union-attr]
            except (OSError, AttributeError):
                self._lines[key] = []
        return self._lines[key]

    def snippet(self, repo: str, path: str, line: int) -> str:
        ls = self.lines(repo, path)
        s = ls[line - 1].strip() if 0 < line <= len(ls) else ""
        return s if len(s) <= 160 else s[:157] + "..."

    # ------------------------------------------------------- target resolve
    def resolve_target(self, text: str, repo: str | None = None) -> Resolution:
        t = text.strip()
        m = ROUTE_Q.match(t)
        if m:
            return self._resolve_route(m.group(1), m.group(2))
        only_repo = None
        m2 = re.match(r"^([\w-]+):([\w./]+)$", t)
        if m2 and self.cfg.repo(m2.group(1)):
            only_repo, t = m2.group(1), m2.group(2)
        m3 = re.match(r"^([\w./-]+\.py):([\w.]+)$", t)  # path.py:Symbol
        if m3:
            path, name = m3.groups()
            cands = [
                d
                for (r, p), ds in self.defs_in_file.items()
                if p.endswith(path) and (only_repo or r) == r
                for d in ds
                if d.qname.endswith("." + name) or d.qname == name
            ]
            return self._wrap(cands, exact=True)
        repo = only_repo or repo
        exact = [
            d
            for d in self.by_short.get(t.rsplit(".", 1)[-1], [])
            if d.qname == t and (not repo or d.repo == repo)
        ]
        if exact:
            return self._wrap(exact, exact=True)
        # re-exported / aliased paths: libx.parse -> libx.core.parse
        seen: list[Def] = []
        for r in [repo] if repo else [rc.name for rc in self.cfg.repos]:
            cr, cq = self.canonical(r, t)
            d = self.defs.get((cr, cq)) if cr else None
            if d is not None and d not in seen:
                seen.append(d)
        if seen:
            return self._wrap(seen, exact=True)
        short = t.rsplit(".", 1)[-1]
        suffix = [
            d
            for d in self.by_short.get(short, [])
            if (d.qname == t or d.qname.endswith("." + t)) and (not repo or d.repo == repo)
        ]
        non_mod = [d for d in suffix if d.kind != "module"]
        return self._wrap(non_mod or suffix, exact=False)

    def _wrap(self, defs: list[Def], exact: bool) -> Resolution:
        defs = sorted({id(d): d for d in defs}.values(), key=lambda d: (d.is_test, d.repo, d.path, d.line))
        cands = [Candidate("symbol", d.qname, d=d) for d in defs[:8]]
        return Resolution(cands, total=len(defs), exact=exact)

    def _resolve_route(self, method: str | None, path: str) -> Resolution:
        segs = route_segments(path)
        probe = HttpCall("", "", 0, method.upper() if method else None, segs, path, "")
        from ..linker import route_matches

        idx = [i for i, r in enumerate(self.routes) if route_matches(r, probe)]
        if not idx and segs:  # tolerate an unlisted mount prefix
            idx = [
                i
                for i, r in enumerate(self.routes)
                if len(r.segs) > len(segs)
                and (not method or r.method in ("ANY", method.upper()))
                and all(_seg_match(a, b) for a, b in zip(r.segs[-len(segs) :], segs))
            ]
        cands = [
            Candidate(
                "route",
                f"{self.routes[i].method} {self.routes[i].template}",
                route=self.routes[i],
                route_idx=i,
            )
            for i in idx[:8]
        ]
        return Resolution(cands, total=len(idx), exact=True)

    # ----------------------------------------------------------------- uses
    def uses(self, c: Candidate, kinds: set[str] | None = None) -> list[Use]:
        out: dict[tuple, Use] = {}

        def put(ref_repo, path, line, kind, conf, scope):
            if kinds and kind not in kinds:
                return
            k = (ref_repo, path, line, kind)
            if k not in out or (out[k].conf != PRECISE and conf == PRECISE):
                out[k] = Use(ref_repo, path, line, kind, conf, "", scope)

        if c.kind == "route":
            for call in self.route_calls.get(c.route_idx, []):
                put(call.repo, call.path, call.line, "http", HTTP, call.scope)
        else:
            d = c.d
            assert d is not None
            for ref in self.by_prefix.get((d.repo, d.qname), []):
                exact = ref.qname == d.qname
                if d.kind == "module":
                    if ref.kind != "import":
                        continue
                elif not exact and ref.base is not None:
                    continue  # typed member access is a use of the member, not of the class
                if ref.target_repo != d.repo:
                    continue
                if (ref.repo, ref.path, ref.line) == (d.repo, d.path, d.line) and ref.kind != "import":
                    continue
                put(ref.repo, ref.path, ref.line, ref.kind, ref.conf, ref.scope)
            if d.kind == "method":
                for ref in self.by_attr.get(d.name, []):
                    put(ref.repo, ref.path, ref.line, ref.kind, INFERRED, ref.scope)
            for ri in self.handler_routes.get((d.repo, d.qname), []):
                for call in self.route_calls.get(ri, []):
                    put(call.repo, call.path, call.line, "http", HTTP, call.scope)
        res = sorted(out.values(), key=lambda u: (u.repo, u.path, u.line, KIND_ORDER.get(u.kind, 9)))
        for u in res:
            u.snippet = self.snippet(u.repo, u.path, u.line)
        return res

    # --------------------------------------------------- impact helpers
    def symbols_in(self, repo: str, path: str, lines: list[int]) -> list[Def]:
        """Innermost non-module definitions containing each line (old-side / indexed lines)."""
        ds = [d for d in self.defs_in_file.get((repo, path), []) if d.kind != "module"]
        found: dict[str, Def] = {}
        for ln in lines:
            best = None
            for d in ds:
                if d.start <= ln <= d.end and (best is None or (d.end - d.start) < (best.end - best.start)):
                    best = d
            if best is not None:
                found[best.qname] = best
        return list(found.values())

    def module_def(self, repo: str, path: str) -> Def | None:
        fi = self.files.get((repo, path))
        return self.defs.get((repo, fi.module)) if fi else None

    def repo_of_paths(self, paths: list[str], hint: str | None = None) -> str | None:
        if hint and self.cfg.repo(hint):
            return hint
        score: dict[str, int] = defaultdict(int)
        for p in paths:
            for rc in self.cfg.repos:
                if (rc.name, p) in self.files:
                    score[rc.name] += 1
        return max(score, key=score.get) if score else None  # type: ignore[arg-type]

    def tests_for(self, uses: list[Use], seeds: list[Def], depth: int = 3) -> list[TestHit]:
        hits: dict[tuple[str, str], TestHit] = {}
        for u in uses:
            fi = self.files.get((u.repo, u.path))
            if fi and fi.is_test:
                k = (u.repo, u.path)
                if k not in hits:
                    hits[k] = TestHit(
                        u.repo,
                        u.path,
                        f"{u.kind}s it at line {u.line}"
                        if u.kind != "ref"
                        else f"references it at line {u.line}",
                        True,
                    )
        # transitive: tests importing (<=depth hops) a module that contains a non-test use
        start = {
            (u.repo, self.files[(u.repo, u.path)].module)
            for u in uses
            if (u.repo, u.path) in self.files and not self.files[(u.repo, u.path)].is_test
        }
        mod_path = {(k[0], fi.module): k[1] for k, fi in self.files.items()}
        seen = set(start)
        q = deque((m, 0, [m]) for m in start)
        while q:
            m, dp, chain = q.popleft()
            if dp >= depth:
                continue
            for imp in sorted(self.importers.get(m, ())):
                if imp in seen:
                    continue
                seen.add(imp)
                path = mod_path.get(imp)
                nchain = chain + [imp]
                fi = self.files.get((imp[0], path)) if path else None
                if fi and path and fi.is_test and (imp[0], path) not in hits:  # fi implies path
                    via = " -> ".join(f"{x[1]}" for x in reversed(nchain))
                    hits[(imp[0], path)] = TestHit(imp[0], path, f"imports {via}", False)
                q.append((imp, dp + 1, nchain))
        return sorted(hits.values(), key=lambda h: (not h.direct, h.repo, h.path))

    def repo_dep_edges(self) -> dict[tuple[str, str], int]:
        return dict(self.repo_deps)

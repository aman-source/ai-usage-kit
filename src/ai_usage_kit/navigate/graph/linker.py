"""Cross-repo linking.

* imports -> owning repo (`PackageMap`)
* FastAPI/Flask routes <-> httpx/requests calls (`link_routes`)
"""

from __future__ import annotations

import re
from collections import defaultdict

from .config import NON_OWNER_TOPS
from .model import HttpCall, Route

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


# ---------------------------------------------------------------- packages
class PackageMap:
    """top-level import name -> owning repo; per-repo own top-level names."""

    def __init__(self) -> None:
        self.owner: dict[str, str] = {}
        self.tops: dict[str, set[str]] = defaultdict(set)

    def add(self, repo: str, top: str, explicit: bool = False) -> None:
        if not top:
            return
        self.tops[repo].add(top)
        if explicit or top not in NON_OWNER_TOPS:
            self.owner.setdefault(top, repo)

    def locate(self, from_repo: str, qname: str) -> str | None:
        """Repo that defines `qname`: the caller's own repo wins, then the package owner."""
        top = qname.split(".", 1)[0]
        if top in self.tops.get(from_repo, ()):
            return from_repo
        return self.owner.get(top)


# ------------------------------------------------------------------ routes
_PARAM = re.compile(r"\{[^/{}]*\}|<[^/<>]*>")
_SCHEME = re.compile(r"^[A-Za-z][\w+.-]*://[^/]*")


def route_segments(template: str) -> tuple[str, ...]:
    """/items/{id} and /items/<int:id> -> ("items", "{}")."""
    path = template.split("?", 1)[0]
    segs = []
    for s in path.split("/"):
        if s:
            segs.append("{}" if _PARAM.fullmatch(s) else _PARAM.sub("{}", s))
    return tuple(segs)


def call_segments(tpl: str, base: str = "") -> tuple[str, ...] | None:
    """URL template (dynamic parts already `{}`) -> path segments, or None if no path."""
    t = tpl.strip()
    if _SCHEME.match(t):
        t = _SCHEME.sub("", t, count=1)
    elif t.startswith("{}"):  # f"{BASE}/items/{id}": leading placeholder is the host/base
        t = t[2:]
        if t and not t.startswith("/"):
            return None
    t = re.split(r"[?#]", t, maxsplit=1)[0]
    if not t.startswith("/"):
        t = "/" + t
    full = (base or "").rstrip("/") + t
    segs = tuple(s for s in full.split("/") if s)
    return segs or None


def _seg_match(route_seg: str, call_seg: str) -> bool:
    if route_seg == "{}" or call_seg == "{}":
        return True
    if "{}" in route_seg or "{}" in call_seg:
        src, other = (call_seg, route_seg) if "{}" in call_seg else (route_seg, call_seg)
        pat = "".join(".+" if p == "{}" else re.escape(p) for p in re.split(r"(\{\})", src))
        return re.fullmatch(pat, other) is not None
    return route_seg == call_seg


def route_matches(route: Route, call: HttpCall) -> bool:
    if len(route.segs) != len(call.segs):
        return False
    if call.method and route.method not in ("ANY", call.method):
        return False
    return all(_seg_match(r, c) for r, c in zip(route.segs, call.segs))


def link_routes(routes: list[Route], calls: list[HttpCall]) -> dict[int, list[HttpCall]]:
    """route index -> matching http calls (any repo, incl. test clients)."""
    by_len: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(routes):
        by_len[len(r.segs)].append(i)
    out: dict[int, list[HttpCall]] = defaultdict(list)
    for c in calls:
        for i in by_len.get(len(c.segs), ()):
            if route_matches(routes[i], c):
                out[i].append(c)
    return out


def join_prefix(*parts: str) -> str:
    segs = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(segs) if segs else ""

"""Plain data types shared by the engine, tools and renderers."""

from __future__ import annotations

from dataclasses import dataclass, field

PRECISE = "precise"  # resolved import / definition match
INFERRED = "inferred"  # name, package or local-type match
HTTP = "http"  # route string matched to an httpx/requests call


@dataclass(slots=True)
class Def:
    qname: str
    kind: str  # module | class | function | method | variable
    repo: str
    path: str
    line: int  # the `def`/`class` line (not the decorator)
    start: int  # first line incl. decorators
    end: int
    cls: str | None = None  # enclosing class qname (methods)
    bases: list[str] = field(default_factory=list)  # resolved base qnames (classes)
    is_test: bool = False

    @property
    def name(self) -> str:
        return self.qname.rsplit(".", 1)[-1]


@dataclass(slots=True)
class Ref:
    """One reference to something. Either `qname` is set (resolved through
    imports / module names), or `base`+`attrs` describe a typed receiver whose
    member is resolved at finalize time, or only `attrs` (name-only)."""

    kind: str  # call | import | subclass | decorator | type | ref
    repo: str
    path: str
    line: int
    scope: str  # enclosing qname
    qname: str | None = None
    base: tuple | None = None  # ("inst", q) | ("self", cls_q)
    attrs: tuple[str, ...] = ()
    conf: str = PRECISE
    raw: str | None = None  # qname as written/resolved before re-export following
    target_repo: str | None = None  # repo that defines the resolved target


@dataclass(slots=True)
class Route:
    repo: str
    path: str
    line: int
    method: str  # GET, POST ... or ANY
    template: str  # full path as written, e.g. /api/items/{item_id}
    segs: tuple[str, ...]  # normalised ("{}" for params)
    handler: str | None  # qname of the view function
    framework: str


@dataclass(slots=True)
class HttpCall:
    repo: str
    path: str
    line: int
    method: str | None  # None = unknown
    segs: tuple[str, ...]
    raw: str  # url template as seen
    scope: str


@dataclass(slots=True)
class Use:
    repo: str
    path: str
    line: int
    kind: str
    conf: str
    snippet: str = ""
    scope: str = ""

    @property
    def loc(self) -> str:
        return f"{self.repo}:{self.path}:{self.line}"

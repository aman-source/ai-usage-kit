"""Per-file extraction with `ast`.

Phase A (`scan_file`): module name, import aliases, definitions, string constants.
Phase B (`Visitor`): references, routes, http calls, router prefixes, class attribute types.

Everything that needs the whole index (re-exports, star imports, member lookup through base
classes) is deferred to `Index.finalize`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Callable

from ..linker import HTTP_VERBS, call_segments, route_segments
from ..model import INFERRED, PRECISE, Def, HttpCall, Ref

HTTP_CLIENT_CTORS = {
    "httpx.Client",
    "httpx.AsyncClient",
    "requests.Session",
    "requests.sessions.Session",
    "aiohttp.ClientSession",
    "fastapi.testclient.TestClient",
    "starlette.testclient.TestClient",
}
HTTP_MODULES = {"httpx", "requests", "aiohttp"}
ROUTER_CTORS = {"APIRouter", "FastAPI", "Blueprint", "Flask", "Starlette", "Sanic"}
ROUTE_DECOS = {"get", "post", "put", "delete", "patch", "head", "options", "route", "api_route"}
CLIENT_NAME = re.compile(r"client|session|http|requests", re.I)
URLISH = re.compile(r"^(/|https?://|\{\}/)")


# ------------------------------------------------------------------ results
@dataclass
class R:
    """Result of resolving an expression statically."""

    kind: str  # q (qualified name) | t (typed receiver) | http (http client)
    q: str | None = None
    conf: str = PRECISE
    base: tuple | None = None  # t: ("inst", q) | ("self", cls) | ("super", cls)
    attrs: tuple[str, ...] = ()
    http: str = ""  # http: base path of the client
    alt: "R | None" = None  # q: typed view of the same variable


@dataclass
class RouteRaw:
    repo: str
    path: str
    line: int
    router: str | None  # qname of the router/app variable
    template: str
    methods: list[str]
    handler: str | None
    framework: str


@dataclass
class Include:
    parent: str | None
    child: str
    prefix: str | None
    override: bool  # flask register_blueprint url_prefix replaces the blueprint's own


@dataclass
class FileInfo:
    repo: str
    path: str
    module: str
    is_init: bool
    is_test: bool
    nlines: int = 0
    aliases: dict[str, str] = field(default_factory=dict)
    stars: list[str] = field(default_factory=list)
    top_names: set[str] = field(default_factory=set)
    consts: dict[str, str] = field(default_factory=dict)
    all_names: list[str] | None = None  # module-level __all__, if literal
    defs: list[Def] = field(default_factory=list)

    def resolve_name(self, name: str) -> str | None:
        if name in self.aliases:
            return self.aliases[name]
        if name in self.top_names:
            return f"{self.module}.{name}"
        return None

    def resolve_chain(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            b = self.resolve_chain(node.value)
            return f"{b}.{node.attr}" if b else None
        return None


@dataclass
class Globals:
    """Tables shared between files while visiting."""

    consts: dict[str, str] = field(default_factory=dict)  # qname -> url template
    class_attr_types: dict[tuple[str, str, str], R] = field(default_factory=dict)  # (repo, cls, attr)
    ctor_prefix: dict[tuple[str, str], str] = field(default_factory=dict)  # (repo, router qname) -> prefix
    includes: list[tuple[str, Include]] = field(default_factory=list)  # (repo, include)
    routes: list[RouteRaw] = field(default_factory=list)
    calls: list[HttpCall] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    star_lookup: Callable[[str, FileInfo, str], str | None] = lambda repo, fi, name: None


# --------------------------------------------------------------- module names
def module_of(rel: str, roots: list[str]) -> tuple[str, bool]:
    parts_all = rel.split("/")
    for root in roots:
        if root in (".", ""):
            parts = parts_all
        elif rel.startswith(root.rstrip("/") + "/"):
            parts = parts_all[len(root.rstrip("/").split("/")) :]
        else:
            continue
        parts = parts[:-1] + [parts[-1][:-3]]  # strip .py
        is_init = parts[-1] == "__init__"
        if is_init:
            parts = parts[:-1]
        return ".".join(parts), is_init
    return "", False


def is_test_path(path: str) -> bool:
    p = path.split("/")
    name = p[-1]
    return (
        any(x in ("tests", "test", "testing") for x in p[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def _resolve_from(module: str | None, level: int, pkg: str) -> str | None:
    if level == 0:
        return module or ""
    parts = pkg.split(".") if pkg else []
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    if module:
        base = base + module.split(".")
    return ".".join(base)


def _toplevel(body):
    for n in body:
        if isinstance(n, (ast.If, ast.Try, ast.With, ast.AsyncWith)) or type(n).__name__ == "TryStar":
            subs = [getattr(n, "body", []), getattr(n, "orelse", []), getattr(n, "finalbody", [])]
            subs += [h.body for h in getattr(n, "handlers", [])]
            for s in subs:
                yield from _toplevel(s)
        else:
            yield n


def _targets(node) -> list[str]:
    ts = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in ts if isinstance(t, ast.Name)]


def static_template(node: ast.AST, consts: dict[str, str]) -> str | None:
    """Module-level string constants only (phase A)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                out.append(static_template(v.value, consts) or "{}")  # type: ignore[attr-defined]
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = static_template(node.left, consts), static_template(node.right, consts)
        return None if a is None and b is None else (a or "{}") + (b or "{}")
    return None


# ------------------------------------------------------------------- phase A
def scan_file(repo: str, path: str, source: str, module: str, is_init: bool) -> FileInfo | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    fi = FileInfo(repo, path, module, is_init, is_test_path(path), nlines=source.count("\n") + 1)
    pkg = module if is_init else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    fi.aliases[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    fi.aliases[top] = top
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(node.module, node.level, pkg)
            if base is None:
                continue
            for a in node.names:
                if a.name == "*":
                    fi.stars.append(base)
                else:
                    fi.aliases[a.asname or a.name] = f"{base}.{a.name}" if base else a.name

    body = list(_toplevel(tree.body))
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            fi.top_names.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            fi.top_names.update(_targets(n))
    for n in body:  # string constants (url bases)
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            t = static_template(n.value, fi.consts)
            if t is not None:
                for name in _targets(n):
                    fi.consts[name] = t

    for n in body:  # literal __all__ (drives `from m import *` re-exports)
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and n.value is not None:
            tgs = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in tgs) and isinstance(
                n.value, (ast.List, ast.Tuple)
            ):
                names = [
                    e.value for e in n.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                fi.all_names = (fi.all_names or []) + names if isinstance(n, ast.AugAssign) else names

    fi.defs.append(Def(module, "module", repo, path, 1, 1, fi.nlines, is_test=fi.is_test))

    def mk(node, qname, kind, cls=None):
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return Def(
            qname,
            kind,
            repo,
            path,
            node.lineno,
            start,
            node.end_lineno or node.lineno,
            cls=cls,
            is_test=fi.is_test,
        )

    def do_class(node: ast.ClassDef, qname: str):
        d = mk(node, qname, "class")
        for b in node.bases:
            q = fi.resolve_chain(b)
            if q:
                d.bases.append(q)
        fi.defs.append(d)
        for n in _toplevel(node.body):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fi.defs.append(mk(n, f"{qname}.{n.name}", "method", cls=qname))
            elif isinstance(n, ast.ClassDef):
                do_class(n, f"{qname}.{n.name}")
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                for name in _targets(n):
                    fi.defs.append(mk(n, f"{qname}.{name}", "variable", cls=qname))

    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fi.defs.append(mk(n, f"{module}.{n.name}", "function"))
        elif isinstance(n, ast.ClassDef):
            do_class(n, f"{module}.{n.name}")
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for name in _targets(n):
                if not (name.startswith("__") and name.endswith("__")):
                    fi.defs.append(mk(n, f"{module}.{name}", "variable"))
    return fi


# ------------------------------------------------------------------- phase B
class Scope:
    def __init__(self, qname: str, kind: str, locals_: set[str] | None = None, self_name: str | None = None):
        self.qname = qname
        self.kind = kind  # module | class | func
        self.locals = locals_ or set()
        self.self_name = self_name
        self.vtypes: dict[str, R] = {}
        self.strvars: dict[str, str] = {}


def _params(args: ast.arguments) -> list[ast.arg]:
    out = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    if args.vararg:
        out.append(args.vararg)
    if args.kwarg:
        out.append(args.kwarg)
    return out


def _collect_locals(fn) -> set[str]:
    names = {a.arg for a in _params(fn.args)}
    declared: set[str] = set()
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            declared.update(n.names)
        stack.extend(ast.iter_child_nodes(n))
    return names - declared


def _chain_root(node: ast.AST) -> ast.AST:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node


class Visitor(ast.NodeVisitor):
    def __init__(self, fi: FileInfo, g: Globals):
        self.fi, self.g = fi, g
        self.scopes = [Scope(fi.module, "module")]
        self.classes: list[str] = []
        self.seen: set = set()

    # -- scope helpers
    @property
    def scope(self) -> Scope:
        return self.scopes[-1]

    def func_scopes(self):
        return [s for s in reversed(self.scopes) if s.kind == "func"]

    def lookup_local(self, name: str):
        """-> (is_local, vtype)"""
        for s in self.func_scopes():
            if name in s.locals:
                return True, s.vtypes.get(name)
        return False, None

    def self_name(self) -> str | None:
        for s in self.func_scopes():
            if s.self_name:
                return s.self_name
        return None

    def cur_cls(self) -> str | None:
        return self.classes[-1] if self.classes else None

    # -- expression resolution (pure)
    def rx_name(self, name: str) -> R | None:
        sn = self.self_name()
        if sn and name == sn and self.cur_cls():
            is_local, _ = self.lookup_local(name)
            if is_local:
                return R("t", base=("self", self.cur_cls()))
        is_local, vt = self.lookup_local(name)
        if is_local:
            return vt
        mvt = self.scopes[0].vtypes.get(name)
        q = self.fi.resolve_name(name)
        if q is None:
            sq = self.g.star_lookup(self.fi.repo, self.fi, name)
            return R("q", sq, INFERRED) if sq else None
        return R("q", q, alt=mvt) if (mvt is not None and name in self.fi.top_names) else R("q", q)

    def rx(self, node: ast.AST) -> R | None:
        if isinstance(node, ast.Name):
            return self.rx_name(node.id)
        if isinstance(node, ast.Attribute):
            b = self.rx(node.value)
            if b is None:
                return None
            if b.kind == "q":
                alt = self.extend(b.alt, node.attr) if b.alt else None
                return R("q", f"{b.q}.{node.attr}", b.conf, alt=alt)
            return self.extend(b, node.attr)
        if isinstance(node, ast.Await):
            return self.rx(node.value)
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "super" and self.cur_cls() and not node.args:
                return R("t", base=("super", self.cur_cls()))
            if isinstance(f, ast.Attribute) and f.attr == "test_client":
                return R("http", http="")
            r = self.rx(f)
            if r is not None and r.kind == "q" and r.q:
                if r.q in HTTP_CLIENT_CTORS:
                    return R("http", http=self.client_base(node))
                return R("t", base=("inst", r.q), conf=INFERRED)
        return None

    def extend(self, r: R | None, attr: str) -> R | None:
        if r is None:
            return None
        if r.kind == "t":
            return R("t", base=r.base, attrs=r.attrs + (attr,), conf=r.conf)
        if r.kind == "q":
            return R("q", f"{r.q}.{attr}", r.conf)
        return None

    def client_base(self, call: ast.Call) -> str:
        for kw in call.keywords:
            if kw.arg in ("base_url", "base"):
                t = self.url_template(kw.value)
                segs = call_segments(t) if t else None
                return "/" + "/".join(segs) if segs else ""
        return ""

    def ann_type(self, node: ast.AST | None) -> R | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return self.ann_type(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                return None
        if isinstance(node, (ast.Name, ast.Attribute)):
            r = self.rx(node)
            if r is not None and r.kind == "q" and r.q:
                if r.q in HTTP_CLIENT_CTORS:
                    return R("http")
                return R("t", base=("inst", r.q), conf=INFERRED)
            return None
        if isinstance(node, ast.Subscript):
            head = node.value.id if isinstance(node.value, ast.Name) else getattr(node.value, "attr", "")
            if head in ("Optional", "Union", "Annotated"):
                sl = node.slice
                elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
                for e in elts:
                    t = self.ann_type(e)
                    if t:
                        return t
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self.ann_type(node.left) or self.ann_type(node.right)
        return None

    def type_of(self, value: ast.AST | None) -> R | None:
        if value is None:
            return None
        r = self.rx(value)
        if r is None:
            return None
        if r.kind == "http":
            return r
        if r.kind == "t" and (r.attrs or r.base[0] == "inst"):  # type: ignore[index]
            return r
        return None

    # -- url templates
    def url_template(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.JoinedStr):
            out = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    out.append(str(v.value))
                else:
                    out.append(self.url_template(v.value) or "{}")  # type: ignore[attr-defined]
            return "".join(out)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add):
                a, b = self.url_template(node.left), self.url_template(node.right)
                return None if a is None and b is None else (a or "{}") + (b or "{}")
            if (
                isinstance(node.op, ast.Mod)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
            ):
                return re.sub(r"%\(\w+\)s|%[sdif]", "{}", node.left.value)
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if (
                fname == "format"
                and isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Constant)
                and isinstance(f.value.value, str)
            ):
                return re.sub(r"\{[^{}]*\}", "{}", f.value.value)
            if fname == "urljoin" and len(node.args) == 2:
                a, b = self.url_template(node.args[0]) or "{}", self.url_template(node.args[1]) or "{}"
                return a.rstrip("/") + "/" + b.lstrip("/")
            if fname in ("getenv", "get") and len(node.args) >= 2:
                return self.url_template(node.args[1])
            return None
        if isinstance(node, ast.Name):
            for s in self.func_scopes():
                if node.id in s.strvars:
                    return s.strvars[node.id]
                if node.id in s.locals:
                    return None
            if node.id in self.fi.consts:
                return self.fi.consts[node.id]
            q = self.fi.aliases.get(node.id)
            return self.g.consts.get(q) if q else None
        if isinstance(node, ast.Attribute):
            r = self.rx(node)
            if r is not None and r.kind == "q" and r.q:
                return self.g.consts.get(r.q)
        return None

    # -- emission
    def add(self, kind: str, line: int, qname=None, base=None, attrs=(), conf=PRECISE) -> None:
        key = (kind, line, qname, base, attrs)
        if key in self.seen:
            return
        self.seen.add(key)
        self.g.refs.append(
            Ref(
                kind,
                self.fi.repo,
                self.fi.path,
                line,
                self.scope.qname,
                qname=qname,
                base=base,
                attrs=tuple(attrs),
                conf=conf,
            )
        )

    def emit_r(self, r: R, kind: str, line: int) -> None:
        if r.kind == "q":
            self.add(kind, line, qname=r.q, conf=r.conf)
            if r.alt is not None:
                self.emit_r(r.alt, kind, line)
        elif r.kind == "t" and r.attrs:
            self.add(
                kind,
                line,
                base=r.base,
                attrs=r.attrs,
                conf=PRECISE if r.base[0] in ("self", "super") else INFERRED,  # type: ignore[index]
            )

    def emit_target(self, node: ast.AST, kind: str) -> None:
        if not isinstance(node, (ast.Name, ast.Attribute)):
            self.visit(node)
            return
        line = node.end_lineno if isinstance(node, ast.Attribute) else node.lineno
        r = self.rx(node)
        if r is None:
            if isinstance(node, ast.Attribute):
                self.add(kind, line or node.lineno, attrs=(node.attr,), conf=INFERRED)
                self.visit(node.value)
            return
        self.emit_r(r, kind, line or node.lineno)
        root = _chain_root(node)
        if not isinstance(root, ast.Name):
            self.visit(root)

    def emit_annotation(self, node: ast.AST | None) -> None:
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                inner = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return
            ast.increment_lineno(inner, node.lineno - 1)
            self.emit_annotation(inner)
        elif isinstance(node, (ast.Name, ast.Attribute)):
            self.emit_target(node, "type")
        elif isinstance(node, ast.Subscript):
            self.emit_annotation(node.value)
            self.emit_annotation(node.slice)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for e in node.elts:
                self.emit_annotation(e)
        elif isinstance(node, ast.BinOp):
            self.emit_annotation(node.left)
            self.emit_annotation(node.right)

    # -- visitors: names / calls
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.emit_target(node, "ref")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.emit_target(node, "ref")
            return
        r = self.rx(node)
        if r is not None and r.kind == "q":
            self.emit_r(r, "ref", node.end_lineno or node.lineno)
        root = _chain_root(node)
        if not isinstance(root, ast.Name):
            self.visit(root)

    def visit_Call(self, node: ast.Call) -> None:
        self.try_http(node)
        self.try_router(node)
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            self.emit_target(node.func, "call")
        else:
            self.visit(node.func)
        for a in node.args:
            self.visit(a)
        for k in node.keywords:
            self.visit(k.value)

    # -- imports
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.add("import", getattr(a, "lineno", node.lineno), qname=a.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        pkg = self.fi.module if self.fi.is_init else self.fi.module.rpartition(".")[0]
        base = _resolve_from(node.module, node.level, pkg)
        if base is None or base == "__future__":
            return
        for a in node.names:
            q = base if a.name == "*" else (f"{base}.{a.name}" if base else a.name)
            self.add("import", getattr(a, "lineno", node.lineno), qname=q)

    # -- definitions
    def _decorators(self, node) -> None:
        for d in node.decorator_list:
            if isinstance(d, ast.Call):
                self.emit_target(d.func, "decorator")
                for a in d.args:
                    self.visit(a)
                for k in d.keywords:
                    self.visit(k.value)
            else:
                self.emit_target(d, "decorator")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._decorators(node)
        for b in node.bases:
            self.emit_target(b, "subclass")
        for k in node.keywords:
            self.visit(k.value)
        cls_q = f"{self.scope.qname}.{node.name}"
        self.prescan_class(node, cls_q)
        self.scopes.append(Scope(cls_q, "class"))
        self.classes.append(cls_q)
        for st in node.body:
            self.visit(st)
        self.classes.pop()
        self.scopes.pop()

    def visit_FunctionDef(self, node) -> None:
        self.route_scan(node)
        self._decorators(node)
        self.emit_annotation(node.returns)
        for a in _params(node.args):
            self.emit_annotation(a.annotation)
        for d in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(d)
        in_class = self.scope.kind == "class"
        params = _params(node.args)
        is_static = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list)
        self_name = params[0].arg if in_class and params and not is_static else None
        sc = Scope(f"{self.scope.qname}.{node.name}", "func", _collect_locals(node), self_name)
        for a in params:
            t = self.ann_type(a.annotation)
            if t is not None:
                sc.vtypes[a.arg] = t
        self.scopes.append(sc)
        for st in node.body:
            self.visit(st)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    # -- assignments (types, router ctors, string vars)
    def bind(self, target: ast.AST, t: R | None, s: str | None) -> None:
        if isinstance(target, ast.Name):
            sc = self.scope
            if sc.kind == "class":
                return
            if t is not None:
                sc.vtypes[target.id] = t
            else:
                sc.vtypes.pop(target.id, None)
            if s is not None and "/" in s:
                sc.strvars[target.id] = s
        elif isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                self.bind(e, None, None)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        self.note_router_ctor(node.targets, node.value)
        t = self.type_of(node.value)
        s = (
            self.url_template(node.value)
            if isinstance(node.value, (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Call))
            else None
        )
        for tg in node.targets:
            self.bind(tg, t, s)
            if not isinstance(tg, ast.Name):
                self.visit(tg)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.emit_annotation(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self.note_router_ctor([node.target], node.value)
        t = self.ann_type(node.annotation) or self.type_of(node.value)
        self.bind(node.target, t, None)
        if not isinstance(node.target, ast.Name):
            self.visit(node.target)

    def visit_With(self, node) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.bind(item.optional_vars, self.type_of(item.context_expr), None)
        for st in node.body:
            self.visit(st)

    visit_AsyncWith = visit_With

    def prescan_class(self, node: ast.ClassDef, cls_q: str) -> None:
        """Record `self.x = Foo()` / `x: Foo` attribute types for the whole class."""
        repo = self.fi.repo
        for st in node.body:
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                t = self.ann_type(st.annotation)
                if t is not None:
                    self.g.class_attr_types[(repo, cls_q, st.target.id)] = t
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sc = Scope(f"{cls_q}.{st.name}", "func", {a.arg for a in _params(st.args)})
                for a in _params(st.args):
                    t = self.ann_type(a.annotation)
                    if t is not None:
                        sc.vtypes[a.arg] = t
                self.scopes.append(sc)
                try:
                    for n in ast.walk(st):
                        tgs, val, ann = [], None, None
                        if isinstance(n, ast.Assign):
                            tgs, val = n.targets, n.value
                        elif isinstance(n, ast.AnnAssign):
                            tgs, val, ann = [n.target], n.value, n.annotation
                        for tg in tgs:
                            if (
                                isinstance(tg, ast.Attribute)
                                and isinstance(tg.value, ast.Name)
                                and params_first(st) == tg.value.id
                            ):
                                t = self.ann_type(ann) or self.type_of(val)
                                if t is not None:
                                    self.g.class_attr_types.setdefault((repo, cls_q, tg.attr), t)
                finally:
                    self.scopes.pop()

    # -- routers / routes
    def note_router_ctor(self, targets, value) -> None:
        if not isinstance(value, ast.Call):
            return
        r = self.rx(value.func)
        if r is None or r.kind != "q" or not r.q or r.q.rsplit(".", 1)[-1] not in ROUTER_CTORS:
            return
        prefix = ""
        for kw in value.keywords:
            if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant):
                prefix = str(kw.value.value)
        for t in targets:
            if isinstance(t, ast.Name):
                self.g.ctor_prefix[(self.fi.repo, f"{self.fi.module}.{t.id}")] = prefix

    def router_q(self, node: ast.AST) -> str | None:
        r = self.rx(node)
        if r is not None and r.kind == "q":
            return r.q
        if isinstance(node, ast.Name):
            return f"{self.fi.module}.{node.id}"
        return None

    def try_router(self, node: ast.Call) -> None:
        f = node.func
        if not isinstance(f, ast.Attribute):
            return
        if f.attr in ("include_router", "register_blueprint") and node.args:
            child = self.router_q(node.args[0])
            if not child:
                return
            prefix = None
            for kw in node.keywords:
                if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant):
                    prefix = str(kw.value.value)
            self.g.includes.append(
                (self.fi.repo, Include(self.router_q(f.value), child, prefix, f.attr == "register_blueprint"))
            )
        elif f.attr in ("add_api_route", "add_url_rule") and node.args:
            a0 = node.args[0]
            if not (isinstance(a0, ast.Constant) and isinstance(a0.value, str)):
                return
            h = (
                node.args[1]
                if len(node.args) > 1
                else next((k.value for k in node.keywords if k.arg in ("view_func", "endpoint")), None)
            )
            hr = self.rx(h) if h is not None else None
            self.g.routes.append(
                RouteRaw(
                    self.fi.repo,
                    self.fi.path,
                    node.lineno,
                    self.router_q(f.value),
                    a0.value,
                    self.methods_of(node, "GET"),
                    hr.q if hr is not None and hr.kind == "q" else None,
                    "flask" if f.attr == "add_url_rule" else "fastapi",
                )
            )

    @staticmethod
    def methods_of(call: ast.Call, default: str) -> list[str]:
        for kw in call.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                ms = [str(e.value).upper() for e in kw.value.elts if isinstance(e, ast.Constant)]
                if ms:
                    return ms
        return [default]

    def route_scan(self, fn) -> None:
        for d in fn.decorator_list:
            if not (
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE_DECOS
            ):
                continue
            path = None
            if d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                path = d.args[0].value
            else:
                for kw in d.keywords:
                    if kw.arg in ("path", "rule") and isinstance(kw.value, ast.Constant):
                        path = str(kw.value.value)
            if path is None or not (path.startswith("/") or path == ""):
                continue
            attr = d.func.attr
            methods = [attr.upper()] if attr in HTTP_VERBS else self.methods_of(d, "GET")
            self.g.routes.append(
                RouteRaw(
                    self.fi.repo,
                    self.fi.path,
                    d.lineno,
                    self.router_q(d.func.value),
                    path,
                    methods,
                    f"{self.scope.qname}.{fn.name}",
                    "flask" if attr == "route" else "fastapi",
                )
            )

    # -- http client calls
    def try_http(self, node: ast.Call) -> None:
        f = node.func
        if not isinstance(f, ast.Attribute):
            return
        meth = f.attr.lower()
        method: str | None = None
        if meth in HTTP_VERBS:
            method = meth.upper()
            url_node = (
                node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "url"), None)
            )
        elif (
            meth in ("request", "stream")
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.upper() in {v.upper() for v in HTTP_VERBS}
        ):
            method, url_node = node.args[0].value.upper(), node.args[1]
        else:
            return
        recv = self.rx(f.value)
        base, known = "", False
        if recv is not None and recv.kind == "q" and recv.q in HTTP_MODULES:
            known = True
        elif recv is not None and recv.kind == "q" and recv.alt is not None and recv.alt.kind == "http":
            known, base = True, recv.alt.http
        elif recv is not None and recv.kind == "http":
            known, base = True, recv.http
        elif (
            recv is not None
            and recv.kind == "t"
            and recv.base
            and recv.base[0] == "self"
            and len(recv.attrs) == 1
        ):
            t = self.g.class_attr_types.get((self.fi.repo, recv.base[1], recv.attrs[0]))
            if t is not None and t.kind == "http":
                known, base = True, t.http
        tpl = self.url_template(url_node)
        if tpl is None:
            return
        if not known:
            name = f.value.id if isinstance(f.value, ast.Name) else getattr(f.value, "attr", "")
            if not (CLIENT_NAME.search(name or "") and URLISH.match(tpl.strip())):
                return
        segs = call_segments(tpl, base)
        if not segs:
            return
        self.g.calls.append(
            HttpCall(self.fi.repo, self.fi.path, node.lineno, method, segs, tpl, self.scope.qname)
        )


def params_first(fn) -> str | None:
    ps = _params(fn.args)
    return ps[0].arg if ps else None


def visit_file(fi: FileInfo, source: str, g: Globals) -> None:
    tree = ast.parse(source)
    Visitor(fi, g).visit(tree)


__all__ = [
    "FileInfo",
    "Globals",
    "R",
    "RouteRaw",
    "Include",
    "scan_file",
    "visit_file",
    "module_of",
    "is_test_path",
    "route_segments",
]

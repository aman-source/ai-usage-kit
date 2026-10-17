"""Save the finalized engine Index to a SQLite file; open it later so queries run the engine's code unchanged.

Schema 2 (a graph of another schema is ignored and rebuilt):
- `sections(name, data)`: small parts as zlib-compressed JSON (packages, files, re-exports, routes,
  HTTP links, import graph, commits) and `meta` (schema version and build facts);
- `defs`: one row per definition, indexed by qualified name, short name and file, with the orders the engine's
  maps had (`primary_order` = the `defs`/`by_short` order, `file_order` = the `defs_in_file` order);
- `ref_chunks`: references in compressed chunks, `p` chunks per target repo and module (first two name parts)
  for the engine's `by_prefix` lookups, `a` chunks per attribute name for its name-only `by_attr` lookups.
`open_graph(...).index(cfg)` returns an Index whose large maps read rows on demand, so a question loads only
the definitions and reference chunks it touches. Objects are shared per row within one opened graph.
"""

from __future__ import annotations

import json
import os
import sqlite3
import zlib
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import fields
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from ai_usage_kit.navigate.graph.config import Config
from ai_usage_kit.navigate.graph.engine.collect import FileInfo
from ai_usage_kit.navigate.graph.engine.index import Index
from ai_usage_kit.navigate.graph.model import Def, HttpCall, Ref, Route

SCHEMA = 2
REGISTERED, NAME_ONLY = 1, 2
_DEF_FIELDS = [f.name for f in fields(Def)]
_REF_FIELDS = [f.name for f in fields(Ref)]
_TUPLE_FIELDS = {"base", "attrs", "segs"}
_TABLES = ("defs", "refs")


# ---------------------------------------------------------------------------------------------- dump / load


def dump(idx: Index) -> dict[str, Any]:
    """The query state of a built Index as plain JSON-able data."""
    pool: list[Def] = []
    pos: dict[int, int] = {}

    def ref_def(d: Def) -> int:
        if id(d) not in pos:
            pos[id(d)] = len(pool)
            pool.append(d)
        return pos[id(d)]

    defs = [[repo, qname, ref_def(d)] for (repo, qname), d in idx.defs.items()]
    in_file = [[repo, path, [ref_def(d) for d in ds]] for (repo, path), ds in idx.defs_in_file.items()]
    by_short = [[name, [ref_def(d) for d in ds]] for name, ds in idx.by_short.items()]
    name_only = {id(r) for refs in idx.by_attr.values() for r in refs}
    ref_rows, flags = [], []
    for ref in idx.g.refs:
        flag = REGISTERED if ref.target_repo is not None else NAME_ONLY if id(ref) in name_only else 0
        if flag:
            ref_rows.append(_row(ref))
            flags.append(flag)
    call_pos = {id(c): i for i, c in enumerate(idx.http_calls)}
    return {
        "pm": {"owner": dict(idx.pm.owner), "tops": {r: sorted(t) for r, t in idx.pm.tops.items()}},
        "files": [[r, p, fi.module, fi.is_init, fi.is_test] for (r, p), fi in idx.files.items()],
        "defs": {"pool": [_row(d) for d in pool], "defs": defs, "in_file": in_file, "by_short": by_short},
        "reexports": [[r, k, t] for (r, k), t in idx.reexports.items()],
        "routes": [_row(r) for r in idx.routes],
        "refs": {"rows": ref_rows, "flags": flags},
        "http": {
            "calls": [_row(c) for c in idx.http_calls],
            "route_calls": [[ri, [call_pos[id(c)] for c in cs]] for ri, cs in idx.route_calls.items()],
            "handler_routes": [[r, q, list(ris)] for (r, q), ris in idx.handler_routes.items()],
        },
        "importers": [[r, m, sorted([list(x) for x in srcs])] for (r, m), srcs in idx.importers.items()],
        "commits": dict(idx.commits),
        "dirty": dict(idx.dirty),
        "stats": dict(idx.stats),
    }


def load(state: Mapping[str, Any], cfg: Config) -> Index:
    """A fully in-memory Index answering exactly like the one dumped. ValueError for a malformed state."""
    try:
        idx = Index(cfg)
        _fill_small(idx, state)
        defs = state.get("defs")
        if defs is not None:
            pool = [_make(Def, row) for row in defs["pool"]]
            for repo, qname, i in defs["defs"]:
                idx.defs[(repo, qname)] = pool[i]
            for repo, path, ids in defs["in_file"]:
                idx.defs_in_file[(repo, path)] = [pool[i] for i in ids]
            for name, ids in defs["by_short"]:
                idx.by_short[name] = [pool[i] for i in ids]
        refs = state.get("refs")
        if refs is not None:
            for row, flag in zip(refs["rows"], refs["flags"], strict=True):
                ref = _make(Ref, row)
                idx.g.refs.append(ref)
                if flag == REGISTERED:
                    idx._register(ref.target_repo, ref.qname, ref)
                elif flag == NAME_ONLY:
                    idx.by_attr[ref.attrs[-1]].append(ref)
        return idx
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError(f"malformed graph: {type(error).__name__}") from None


def _fill_small(idx: Index, state: Mapping[str, Any]) -> None:
    pm = state.get("pm")
    if pm is not None:
        idx.pm.owner = dict(pm["owner"])
        idx.pm.tops = defaultdict(set, {r: set(t) for r, t in pm["tops"].items()})
    for r, p, module, is_init, is_test in state.get("files", []):
        idx.files[(r, p)] = FileInfo(r, p, module, bool(is_init), bool(is_test))
    for r, k, t in state.get("reexports", []):
        idx.reexports[(r, k)] = t
    idx.routes = [_make(Route, row) for row in state.get("routes", [])]
    http = state.get("http")
    if http is not None:
        idx.http_calls = [_make(HttpCall, row) for row in http["calls"]]
        idx.route_calls = {int(ri): [idx.http_calls[i] for i in cis] for ri, cis in http["route_calls"]}
        for r, q, ris in http["handler_routes"]:
            idx.handler_routes[(r, q)] = list(ris)
    for r, m, srcs in state.get("importers", []):
        idx.importers[(r, m)] = {(a, b) for a, b in srcs}
    idx.commits = dict(state.get("commits", {}))
    idx.dirty = dict(state.get("dirty", {}))
    idx.stats = dict(state.get("stats", {}))


# ---------------------------------------------------------------------------------------------- the file


def write_db(path: Path, state: Mapping[str, Any], meta: Mapping[str, Any]) -> None:
    """Write a new graph file: built under a temporary name, then renamed into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.unlink(missing_ok=True)
    con = sqlite3.connect(temp)
    try:
        with con:
            con.execute("CREATE TABLE sections (name TEXT PRIMARY KEY, data BLOB NOT NULL)")
            con.execute(
                "CREATE TABLE defs (id INTEGER PRIMARY KEY, repo TEXT, qname TEXT, name TEXT, path TEXT, "
                "primary_order INTEGER, file_order INTEGER, row TEXT NOT NULL)"
            )
            con.execute(
                "CREATE TABLE ref_chunks (kind TEXT, repo TEXT, bucket TEXT, data BLOB NOT NULL, "
                "PRIMARY KEY (kind, repo, bucket))"
            )
            rows = [(name, _pack(value)) for name, value in state.items() if name not in _TABLES]
            rows.append(("meta", _pack({**meta, "schema": SCHEMA})))
            con.executemany("INSERT INTO sections (name, data) VALUES (?, ?)", rows)
            con.executemany("INSERT INTO defs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", _def_rows(state.get("defs")))
            con.executemany("INSERT INTO ref_chunks VALUES (?, ?, ?, ?)", _ref_chunks(state.get("refs")))
            con.execute("CREATE INDEX defs_by_qname ON defs (repo, qname)")
            con.execute("CREATE INDEX defs_by_name ON defs (name)")
            con.execute("CREATE INDEX defs_by_path ON defs (repo, path)")
    finally:
        con.close()
    os.chmod(temp, 0o600)  # graph contents stay private to the user
    os.replace(temp, path)


def _def_rows(defs: Mapping[str, Any] | None) -> list[tuple[Any, ...]]:
    if not defs:
        return []
    primary: dict[int, int] = {}
    for order, (_repo, _qname, i) in enumerate(defs["defs"]):
        primary.setdefault(i, order)
    in_file: dict[int, int] = {}
    for _repo, _path, ids in defs["in_file"]:
        for order, i in enumerate(ids):
            in_file.setdefault(i, order)
    q, r, p = _DEF_FIELDS.index("qname"), _DEF_FIELDS.index("repo"), _DEF_FIELDS.index("path")
    out = []
    for i, row in enumerate(defs["pool"]):
        qname = row[q]
        text = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        out.append((i, row[r], qname, qname.rsplit(".", 1)[-1], row[p], primary.get(i), in_file.get(i), text))
    return out


def _ref_chunks(refs: Mapping[str, Any] | None) -> list[tuple[str, str, str, bytes]]:
    if not refs:
        return []
    q, t, a = _REF_FIELDS.index("qname"), _REF_FIELDS.index("target_repo"), _REF_FIELDS.index("attrs")
    chunks: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for i, (row, flag) in enumerate(zip(refs["rows"], refs["flags"], strict=True)):
        if flag == REGISTERED:
            chunks[("p", row[t], _bucket(row[q]))].append([i, row])
        elif flag == NAME_ONLY:
            chunks[("a", "", row[a][-1])].append([i, row])
    return [(kind, repo, bucket, _pack(items)) for (kind, repo, bucket), items in chunks.items()]


def _bucket(qname: str) -> str:
    return ".".join(qname.split(".")[:2])


def read_meta(path: Path) -> dict[str, Any] | None:
    """The build facts of a graph file, or None for a missing, corrupt or other-schema file."""
    graph = open_graph(path)
    if graph is None:
        return None
    graph.close()
    return graph.meta


def open_graph(path: Path) -> Graph | None:
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute("SELECT data FROM sections WHERE name = 'meta'").fetchone()
        meta = _unpack(row[0]) if row is not None else None
        con.execute("SELECT count(*) FROM defs WHERE id = -1").fetchone()  # the schema 2 tables exist
        con.execute("SELECT count(*) FROM ref_chunks WHERE kind = ''").fetchone()
    except (sqlite3.Error, zlib.error, ValueError):
        con.close()
        return None
    if not isinstance(meta, dict) or meta.get("schema") != SCHEMA:
        con.close()
        return None
    return Graph(con, meta)


_Chunk = list[tuple[int, Ref]]


class Graph:
    """An open, read-only graph file. Close it when the question is answered (Windows keeps open files)."""

    def __init__(self, con: sqlite3.Connection, meta: dict[str, Any]) -> None:
        self.con = con
        self.meta = meta
        self.chunks_read = 0
        self._defs: dict[int, Def] = {}
        self._refs: dict[int, Ref] = {}
        self._chunks: dict[tuple[str, str, str], _Chunk] = {}

    def close(self) -> None:
        self.con.close()

    def index(self, cfg: Config) -> Index:
        """An Index whose definition and reference maps read rows on demand. ValueError if malformed."""
        try:
            rows = self.con.execute("SELECT name, data FROM sections WHERE name != 'meta'").fetchall()
            state = {name: _unpack(data) for name, data in rows}
            idx = Index(cfg)
            _fill_small(idx, state)
        except (
            sqlite3.Error,
            zlib.error,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            AttributeError,
        ) as error:
            raise ValueError(f"malformed graph: {type(error).__name__}") from None
        lazy: dict[str, object] = {
            "defs": _Defs(self),
            "by_short": _ByShort(self),
            "defs_in_file": _DefsInFile(self),
            "by_prefix": _ByPrefix(self),
            "by_attr": _ByAttr(self),
        }
        for name, value in lazy.items():
            setattr(idx, name, value)
        return idx

    def defs_from(self, rows: list[tuple[int, str]]) -> list[Def]:
        """Definition rows -> shared Def objects."""
        out = []
        for i, row in rows:
            d = self._defs.get(i)
            if d is None:
                d = self._defs[i] = _make(Def, json.loads(row))
            out.append(d)
        return out

    def chunk(self, kind: str, repo: str, bucket: str) -> _Chunk:
        """(build order, Ref) pairs of one reference chunk; empty when there is none."""
        key = (kind, repo, bucket)
        if key not in self._chunks:
            row = self.con.execute(
                "SELECT data FROM ref_chunks WHERE kind = ? AND repo = ? AND bucket = ?", key
            ).fetchone()
            pairs: _Chunk = []
            if row is not None:
                self.chunks_read += 1
                for i, values in _unpack(row[0]):
                    ref = self._refs.get(i)
                    if ref is None:
                        ref = self._refs[i] = _make(Ref, values)
                    pairs.append((i, ref))
            self._chunks[key] = pairs
        return self._chunks[key]

    def buckets(self, kind: str, repo: str, prefix: str) -> list[str]:
        """Buckets equal to `prefix` or below it (prefix.*)."""
        rows = self.con.execute(
            "SELECT bucket FROM ref_chunks WHERE kind = ? AND repo = ? AND (bucket = ? OR "
            "(bucket >= ? AND bucket < ?))",
            (kind, repo, prefix, prefix + ".", prefix + "/"),
        ).fetchall()
        return [b for (b,) in rows]


_SELECT = "SELECT id, row FROM defs WHERE "


class _Defs(Mapping[tuple[str, str], Def]):
    """idx.defs: (repo, qname) -> the first definition of that name."""

    def __init__(self, graph: Graph) -> None:
        self.g = graph
        self.memo: dict[tuple[str, str], Def | None] = {}

    def get(self, key: tuple[str, str], default: Any = None) -> Any:
        if key not in self.memo:
            rows = self.g.con.execute(
                _SELECT
                + "repo = ? AND qname = ? AND primary_order IS NOT NULL ORDER BY primary_order LIMIT 1",
                key,
            ).fetchall()
            self.memo[key] = self.g.defs_from(rows)[0] if rows else None
        found = self.memo[key]
        return default if found is None else found

    def __getitem__(self, key: tuple[str, str]) -> Def:
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        result: Def = found
        return result

    def __contains__(self, key: object) -> bool:
        return isinstance(key, tuple) and len(key) == 2 and self.get(key) is not None

    def _all(self) -> list[Def]:
        rows = self.g.con.execute(_SELECT + "primary_order IS NOT NULL ORDER BY primary_order").fetchall()
        return self.g.defs_from(rows)

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter([(d.repo, d.qname) for d in self._all()])

    def __len__(self) -> int:
        row = self.g.con.execute("SELECT count(*) FROM defs WHERE primary_order IS NOT NULL").fetchone()
        return int(row[0])

    def values(self) -> list[Def]:  # type: ignore[override]
        return self._all()

    def items(self) -> list[tuple[tuple[str, str], Def]]:  # type: ignore[override]
        return [((d.repo, d.qname), d) for d in self._all()]

    def matching(self, pattern: str) -> list[Def]:
        """Definitions whose qualified or short name matches a `*`/`?` pattern (SQL GLOB, then fnmatch)."""
        if "[" in pattern:
            candidates = self._all()
        else:
            rows = self.g.con.execute(
                _SELECT
                + "primary_order IS NOT NULL AND (qname GLOB ? OR name GLOB ?) ORDER BY primary_order",
                (pattern, pattern),
            ).fetchall()
            candidates = self.g.defs_from(rows)
        return [d for d in candidates if fnmatchcase(d.qname, pattern) or fnmatchcase(d.name, pattern)]


class _ByShort(Mapping[str, list[Def]]):
    """idx.by_short: short name -> definitions in the engine's order (empty list when none)."""

    def __init__(self, graph: Graph) -> None:
        self.g = graph
        self.memo: dict[str, list[Def]] = {}

    def __getitem__(self, name: str) -> list[Def]:
        if name not in self.memo:
            rows = self.g.con.execute(
                _SELECT + "name = ? AND primary_order IS NOT NULL ORDER BY primary_order", (name,)
            ).fetchall()
            self.memo[name] = self.g.defs_from(rows)
        return self.memo[name]

    def get(self, name: str, default: Any = None) -> Any:
        found = self[name]
        return found if found else default

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and bool(self[name])

    def _names(self) -> list[str]:
        rows = self.g.con.execute(
            "SELECT name FROM defs WHERE primary_order IS NOT NULL GROUP BY name ORDER BY min(primary_order)"
        ).fetchall()
        return [n for (n,) in rows]

    def __iter__(self) -> Iterator[str]:
        return iter(self._names())

    def __len__(self) -> int:
        return len(self._names())

    def keys(self) -> list[str]:  # type: ignore[override]
        return self._names()


class _DefsInFile(Mapping[tuple[str, str], list[Def]]):
    """idx.defs_in_file: (repo, path) -> every definition in that file, in file order (empty when none)."""

    def __init__(self, graph: Graph) -> None:
        self.g = graph
        self.memo: dict[tuple[str, str], list[Def]] = {}

    def __getitem__(self, key: tuple[str, str]) -> list[Def]:
        if key not in self.memo:
            rows = self.g.con.execute(
                _SELECT + "repo = ? AND path = ? AND file_order IS NOT NULL ORDER BY file_order", key
            ).fetchall()
            self.memo[key] = self.g.defs_from(rows)
        return self.memo[key]

    def get(self, key: tuple[str, str], default: Any = None) -> Any:
        found = self[key]
        return found if found else default

    def __contains__(self, key: object) -> bool:
        return isinstance(key, tuple) and len(key) == 2 and bool(self[key])

    def _keys(self) -> list[tuple[str, str]]:
        rows = self.g.con.execute(
            "SELECT DISTINCT repo, path FROM defs WHERE file_order IS NOT NULL"
        ).fetchall()
        return [(r, p) for r, p in rows]

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._keys())

    def __len__(self) -> int:
        return len(self._keys())

    def items(self) -> list[tuple[tuple[str, str], list[Def]]]:  # type: ignore[override]
        return [(key, self[key]) for key in self._keys()]


class _ByPrefix:
    """idx.by_prefix.get((repo, qname)): references registered under that name or any name below it."""

    def __init__(self, graph: Graph) -> None:
        self.g = graph
        self.memo: dict[tuple[str, str], list[Ref]] = {}

    def get(self, key: tuple[str, str], default: Any = None) -> Any:
        if key not in self.memo:
            repo, qname = key
            buckets = [_bucket(qname)] if "." in qname else self.g.buckets("p", repo, qname)
            below = qname + "."
            pairs = [
                (i, ref)
                for bucket in buckets
                for i, ref in self.g.chunk("p", repo, bucket)
                if ref.target_repo == repo and (ref.qname == qname or (ref.qname or "").startswith(below))
            ]
            pairs.sort(key=lambda pair: pair[0])
            self.memo[key] = [ref for _, ref in pairs]
        return self.memo[key] or default


class _ByAttr:
    """idx.by_attr.get(name): name-only references to an attribute or method called `name`."""

    def __init__(self, graph: Graph) -> None:
        self.g = graph

    def get(self, name: str, default: Any = None) -> Any:
        return [ref for _, ref in self.g.chunk("a", "", name)] or default


# ---------------------------------------------------------------------------------------------- helpers


def _pack(value: object) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 6)


def _unpack(data: object) -> Any:
    if not isinstance(data, bytes):
        raise ValueError("section is not a blob")
    return json.loads(zlib.decompress(data).decode("utf-8"))


def _row(obj: object) -> list[Any]:
    return [getattr(obj, f.name) for f in fields(obj)]  # type: ignore[arg-type]


def _make(cls: type[Any], row: list[Any]) -> Any:
    names = [f.name for f in fields(cls)]
    if len(row) != len(names):
        raise ValueError(f"{cls.__name__} row has {len(row)} values, expected {len(names)}")
    values = [
        tuple(v) if n in _TUPLE_FIELDS and v is not None else v for n, v in zip(names, row, strict=True)
    ]
    return cls(*values)

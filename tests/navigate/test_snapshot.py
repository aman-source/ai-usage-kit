import json
import sqlite3
from pathlib import Path

import pytest

from ai_usage_kit.navigate import snapshot
from ai_usage_kit.navigate.graph.config import Config, RepoConfig
from ai_usage_kit.navigate.graph.engine.index import Index
from ai_usage_kit.navigate.tools import Tools
from tests.navigate.conftest import World

FLASK = Path(__file__).resolve().parent / "fixtures" / "flask_repos"


def _cands(idx: Index, target: str) -> list[tuple[str, str, str, int]]:
    res = idx.resolve_target(target)
    out = []
    for c in res.candidates:
        if c.d is not None:
            out.append((c.kind, c.d.qname, c.d.path, c.d.line))
        else:
            assert c.route is not None
            out.append((c.kind, c.qname, c.route.path, c.route.line))
    return out


def _uses(idx: Index, target: str) -> list[list[tuple[object, ...]]]:
    res = idx.resolve_target(target)
    return [
        [(u.repo, u.path, u.line, u.kind, u.conf, u.scope, u.snippet) for u in idx.uses(c)]
        for c in res.candidates
    ]


def _tests(idx: Index, target: str) -> list[tuple[str, str, str, bool]]:
    res = idx.resolve_target(target)
    uses = [u for c in res.candidates for u in idx.uses(c)]
    return [(h.repo, h.path, h.reason, h.direct) for h in idx.tests_for(uses, [])]


def _targets(idx: Index) -> list[str]:
    names = {d.qname for d in idx.defs.values()} | set(idx.by_short)
    names |= {f"{r.method} {r.template}" for r in idx.routes}
    return sorted(names)


def _assert_same(a: Index, b: Index) -> None:
    targets = _targets(a)
    assert targets == _targets(b)
    for t in targets:
        assert _cands(a, t) == _cands(b, t), t
        assert _uses(a, t) == _uses(b, t), t
        assert _tests(a, t) == _tests(b, t), t
    for (repo, path), fi in a.files.items():
        for line in range(1, 60):
            got = [d.qname for d in b.symbols_in(repo, path, [line])]
            assert [d.qname for d in a.symbols_in(repo, path, [line])] == got
        assert (a.module_def(repo, path) or None) == (b.module_def(repo, path) or None)
        assert fi.module == b.files[(repo, path)].module and fi.is_test == b.files[(repo, path)].is_test
    assert a.commits == b.commits and a.dirty == b.dirty


def test_round_trip_world(world: World) -> None:
    idx = Index(world.cfg).build()
    state = snapshot.dump(idx)
    json.dumps(state)  # plain JSON
    _assert_same(idx, snapshot.load(state, world.cfg))


def test_round_trip_flask() -> None:
    cfg = Config(repos=[RepoConfig(p.name, p) for p in sorted(FLASK.iterdir())])
    idx = Index(cfg).build()
    _assert_same(idx, snapshot.load(json.loads(json.dumps(snapshot.dump(idx))), cfg))


def _same_answers(a: Index, b: Index, cfg: Config) -> None:
    full, opened = Tools(a, cfg, show_repo=True), Tools(b, cfg, show_repo=True)
    for t in [*_targets(a), "*arse*", "sharedlib.core.*", "nope_zz", "price|parse"]:
        assert full.where(t) == opened.where(t), t
        assert full.who_uses(t) == opened.who_uses(t), t
        assert full.who_uses(t, depth=3) == opened.who_uses(t, depth=3), t
        assert full.impact(symbol=t) == opened.impact(symbol=t), t
    for _, path in a.files:
        assert full.outline(path) == opened.outline(path), path


def test_db_round_trip_opens_lazily_with_identical_answers(world: World, tmp_path: Path) -> None:
    idx = Index(world.cfg).build()
    db = tmp_path / "g" / "graph-1.db"
    snapshot.write_db(db, snapshot.dump(idx), {"head": "abc", "files": {"a.py": [1, 2]}})
    meta = snapshot.read_meta(db)
    assert meta is not None
    assert meta["head"] == "abc" and meta["files"] == {"a.py": [1, 2]} and meta["schema"] == snapshot.SCHEMA
    graph = snapshot.open_graph(db)
    assert graph is not None
    try:
        lazy = graph.index(world.cfg)
        _assert_same(idx, lazy)
        _same_answers(idx, lazy, world.cfg)
    finally:
        graph.close()


def test_db_round_trip_flask(tmp_path: Path) -> None:
    cfg = Config(repos=[RepoConfig(p.name, p) for p in sorted(FLASK.iterdir())])
    idx = Index(cfg).build()
    db = tmp_path / "graph-1.db"
    snapshot.write_db(db, snapshot.dump(idx), {})
    graph = snapshot.open_graph(db)
    assert graph is not None
    try:
        lazy = graph.index(cfg)
        _assert_same(idx, lazy)
        _same_answers(idx, lazy, cfg)
    finally:
        graph.close()


def test_a_query_reads_only_the_chunks_it_needs(world: World, tmp_path: Path) -> None:
    idx = Index(world.cfg).build()
    db = tmp_path / "graph-1.db"
    snapshot.write_db(db, snapshot.dump(idx), {})
    graph = snapshot.open_graph(db)
    assert graph is not None
    try:
        lazy = graph.index(world.cfg)
        Tools(lazy, world.cfg).who_uses("sharedlib.core.price")
        with sqlite3.connect(db) as con:
            total = con.execute("SELECT count(*) FROM ref_chunks").fetchone()[0]
        con.close()
        assert 0 < graph.chunks_read < total
    finally:
        graph.close()


def test_open_graph_rejects_bad_files(tmp_path: Path) -> None:
    assert snapshot.open_graph(tmp_path / "missing.db") is None
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"\x00\x01not sqlite" * 50)
    assert snapshot.open_graph(junk) is None
    assert snapshot.read_meta(junk) is None


def test_other_schema_and_corrupt_sections_are_rejected(tmp_path: Path, world: World) -> None:
    other = tmp_path / "other.db"
    snapshot.write_db(other, {"pm": {"owner": {}, "tops": {}}}, {})
    with sqlite3.connect(other) as con:
        con.execute("UPDATE sections SET data = ? WHERE name = 'meta'", (snapshot._pack({"schema": 999}),))
    con.close()
    assert snapshot.read_meta(other) is None and snapshot.open_graph(other) is None
    broken = tmp_path / "broken.db"
    snapshot.write_db(broken, {"pm": {"owner": {}, "tops": {}}}, {})
    with sqlite3.connect(broken) as con:
        con.execute("UPDATE sections SET data = ? WHERE name = 'pm'", (b"not zlib",))
    con.close()
    graph = snapshot.open_graph(broken)
    assert graph is not None
    try:
        with pytest.raises(ValueError):
            graph.index(world.cfg)
    finally:
        graph.close()


def test_write_db_replaces_nothing_in_place(tmp_path: Path) -> None:
    db = tmp_path / "graph-1.db"
    snapshot.write_db(db, {"pm": {"owner": {}, "tops": {}}}, {"head": "a"})
    assert [p.name for p in tmp_path.iterdir()] == ["graph-1.db"]  # no temp file left behind


def test_build_with_explicit_file_list(world: World) -> None:
    repo = world.root / "sharedlib"
    cfg = Config(repos=[RepoConfig("sharedlib", repo)])
    only = [repo / "src" / "sharedlib" / "core.py", repo / "src" / "sharedlib" / "models.py"]
    idx = Index(cfg).build(files={"sharedlib": only})
    assert sorted(p for _, p in idx.files) == ["src/sharedlib/core.py", "src/sharedlib/models.py"]


@pytest.mark.parametrize("bad", [{"pm": 5}, {"defs": {"pool": [[1]]}}])
def test_load_of_malformed_state_raises_value_error(world: World, bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        snapshot.load(bad, world.cfg)

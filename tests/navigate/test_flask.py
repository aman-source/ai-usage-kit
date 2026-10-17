"""Flask blueprints / add_url_rule <-> requests calls."""

from pathlib import Path

import pytest

from ai_usage_kit.navigate.graph.config import Config, RepoConfig
from ai_usage_kit.navigate.graph.engine.index import Index

FIX = Path(__file__).resolve().parent / "fixtures" / "flask_repos"


@pytest.fixture(scope="module")
def idx():
    return Index(Config(repos=[RepoConfig(p.name, p) for p in sorted(FIX.iterdir())])).build()


def locs(idx, target):
    res = idx.resolve_target(target)
    return {(u.path.rsplit("/", 1)[-1], u.line, u.conf) for c in res.candidates for u in idx.uses(c)}, res


def test_flask_routes_and_prefix_rules(idx):
    got = {(r.method, r.template) for r in idx.routes}
    assert (
        "GET",
        "/v2/items/<int:item_id>",
    ) in got  # register_blueprint(url_prefix=) replaces the blueprint's own /items
    assert ("DELETE", "/v2/items/<int:item_id>") in got and ("GET", "/v2/items/search") in got
    assert ("POST", "/admin/reset") in got  # blueprint's own url_prefix when none is given
    assert ("GET", "/ping") in got and ("GET", "/status") in got  # @app.route default GET, add_url_rule
    assert not any(t.startswith("/items") for _, t in got)  # the overridden prefix must not leak


def test_requests_calls_link_to_flask_routes(idx):
    for target, line in (
        ("GET /v2/items/7", 11),
        ("DELETE /v2/items/7", 15),
        ("GET /v2/items/search", 19),
        ("POST /admin/reset", 23),
        ("GET /ping", 27),
        ("GET /status", 31),
    ):
        got, res = locs(idx, target)
        assert res.candidates, target
        assert ("client.py", line, "http") in got, (target, got)


def test_dict_get_with_a_url_like_key_is_not_an_http_call(idx):
    assert all(c.line != 35 for c in idx.http_calls)


def test_flask_handler_who_uses_includes_http_callers(idx):
    got, res = locs(idx, "flask_svc.items.item")
    assert ("client.py", 11, "http") in got and ("client.py", 15, "http") in got

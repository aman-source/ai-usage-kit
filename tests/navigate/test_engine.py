from ai_usage_kit.navigate.graph.engine.index import Index


def locs(uses):
    return {(u.repo, u.path, u.line, u.kind, u.conf) for u in uses}


def uses_of(idx, target):
    res = idx.resolve_target(target)
    assert len(res.candidates) == 1, [c.qname for c in res.candidates]
    return idx.uses(res.candidates[0])


def test_cross_repo_import_and_call_with_reexport(world):
    idx = Index(world.cfg).build()
    got = locs(uses_of(idx, "sharedlib.core.price"))
    # imported through the package re-export `from sharedlib import price`
    assert ("svc_api", "src/svc_api/routes.py", 2, "import", "precise") in got
    assert ("svc_api", "src/svc_api/routes.py", 12, "call", "precise") in got
    assert ("agent_client", "agent_client/client.py", 26, "call", "precise") in got
    assert ("sharedlib", "tests/test_core.py", 11, "call", "precise") in got


def test_reexported_name_resolves_to_definition(world):
    idx = Index(world.cfg).build()
    assert idx.resolve_target("sharedlib.price").candidates[0].qname == "sharedlib.core.price"


def test_lines_point_at_referencing_line_not_block_start(world):
    idx = Index(world.cfg).build()
    for u in uses_of(idx, "sharedlib.core.price"):
        if u.kind == "call":
            assert "price(" in u.snippet


def test_ambiguous_names_return_both(world):
    idx = Index(world.cfg).build()
    res = idx.resolve_target("parse")
    assert sorted(c.qname for c in res.candidates) == ["sharedlib.util.parse", "svc_api.util.parse"]
    # and each only gets its own uses
    mine = [c for c in res.candidates if c.qname == "svc_api.util.parse"][0]
    assert {(u.path, u.line) for u in idx.uses(mine)} == {
        ("src/svc_api/routes.py", 4),
        ("src/svc_api/routes.py", 18),
    }


def test_self_super_and_typed_attribute_calls(world):
    idx = Index(world.cfg).build()
    got = locs(uses_of(idx, "Pricer.quote"))
    assert ("sharedlib", "src/sharedlib/core.py", 27, "call", "precise") in got  # self.quote
    assert ("agent_client", "agent_client/client.py", 31, "call", "precise") in got  # super().quote
    assert (
        "agent_client",
        "agent_client/client.py",
        26,
        "call",
        "precise",
    ) in got  # self.pricer.quote (typed attr)
    assert ("sharedlib", "tests/test_core.py", 16, "call", "inferred") in got  # Pricer().quote(...)


def test_subclass_use(world):
    idx = Index(world.cfg).build()
    got = locs(uses_of(idx, "Pricer"))
    assert ("agent_client", "agent_client/client.py", 29, "subclass", "precise") in got


def test_routes_prefixes_and_http_links(world):
    idx = Index(world.cfg).build()
    templates = {(r.method, r.template) for r in idx.routes}
    assert ("GET", "/api/v1/orders/{order_id}") in templates  # include_router prefix + APIRouter prefix
    assert ("POST", "/api/v1/orders") in templates
    assert ("GET", "/health") in templates
    got = locs(uses_of(idx, "GET /api/v1/orders/42"))
    assert (
        "agent_client",
        "agent_client/client.py",
        16,
        "http",
        "http",
    ) in got  # client with base_url + f-string
    assert ("svc_api", "tests/test_app.py", 13, "http", "http") in got  # TestClient
    post = locs(uses_of(idx, "POST /api/v1/orders"))
    assert ("agent_client", "agent_client/client.py", 20, "http", "http") in post  # httpx.post(f"{BASE}/...")
    assert not locs(uses_of(idx, "DELETE /api/v1/orders/1")) - {
        ("agent_client", "agent_client/client.py", 23, "http", "http")
    }


def test_handler_symbol_includes_http_callers(world):
    idx = Index(world.cfg).build()
    got = locs(uses_of(idx, "svc_api.routes.get_order"))
    assert ("agent_client", "agent_client/client.py", 16, "http", "http") in got


def test_package_ownership_and_repo_deps(world):
    idx = Index(world.cfg).build()
    assert idx.pm.owner["sharedlib"] == "sharedlib"
    assert idx.repo_deps[("agent_client", "sharedlib")] > 0
    assert idx.repo_deps[("svc_api", "sharedlib")] > 0
    assert ("sharedlib", "svc_api") not in idx.repo_deps


def test_module_who_uses_is_imports_only(world):
    idx = Index(world.cfg).build()
    assert {u.kind for u in uses_of(idx, "sharedlib.core")} == {"import"}


def test_tests_for_includes_direct_and_transitive(world):
    idx = Index(world.cfg).build()
    res = idx.resolve_target("sharedlib.core.price")
    uses = idx.uses(res.candidates[0])
    hits = {(h.repo, h.path): h for h in idx.tests_for(uses, [])}
    assert hits[("sharedlib", "tests/test_core.py")].direct
    # agent_client tests import agent_client.client, which uses price
    assert ("agent_client", "tests/test_client.py") in hits and not hits[
        ("agent_client", "tests/test_client.py")
    ].direct


def test_secret_files_are_never_indexed(world):
    (world.root / "sharedlib" / ".env").write_text("TOKEN='sk-secret-value'\n")
    (world.root / "sharedlib" / "src" / "sharedlib" / "secrets.key").write_text("x = 'sk-secret-value'\n")
    idx = Index(world.cfg).build()
    assert all(not p.endswith(".key") for (_, p) in idx.files)


def test_syntax_errors_do_not_break_indexing(world):
    (world.root / "sharedlib" / "src" / "sharedlib" / "broken.py").write_text("def f(:\n")
    idx = Index(world.cfg).build()
    assert idx.stats["files"] >= 14


def test_star_import_is_inferred(world):
    (world.root / "svc_api" / "src" / "svc_api" / "star.py").write_text(
        "from sharedlib.core import *\n\n\ndef f(o):\n    return price(o)\n"
    )
    idx = Index(world.cfg).build()
    got = locs(uses_of(idx, "sharedlib.core.price"))
    assert ("svc_api", "src/svc_api/star.py", 5, "call", "inferred") in got


def test_star_reexports_follow_all_and_public_names(world):
    lib = world.root / "sharedlib" / "src" / "sharedlib"
    (lib / "_impl.py").write_text(
        "__all__ = ['Widget', 'make']\n\n\nclass Widget:\n    pass\n\n\ndef make():\n    return Widget()\n\n\ndef hidden():\n    pass\n"
    )
    (lib / "_more.py").write_text("def helper():\n    pass\n\n\ndef _private():\n    pass\n")
    (lib / "api.py").write_text("from ._impl import *\nfrom ._more import *\n")
    (lib / "__init__.py").write_text((lib / "__init__.py").read_text() + "from .api import *\n")
    (world.root / "agent_client" / "agent_client" / "use.py").write_text(
        "import sharedlib\n\nw = sharedlib.Widget()\nsharedlib.helper()\nsharedlib.make()\n"
    )
    idx = Index(world.cfg).build()
    assert (
        idx.resolve_target("sharedlib.Widget").candidates[0].qname == "sharedlib._impl.Widget"
    )  # via api.py -> __init__ star chain
    assert idx.resolve_target("sharedlib.helper").candidates[0].qname == "sharedlib._more.helper"
    assert idx.canonical("sharedlib", "sharedlib.hidden") == (
        "sharedlib",
        "sharedlib.hidden",
    )  # not in __all__: not re-exported
    assert idx.canonical("sharedlib", "sharedlib._private") == ("sharedlib", "sharedlib._private")
    got = locs(uses_of(idx, "sharedlib._impl.Widget"))
    assert ("agent_client", "agent_client/use.py", 3, "call", "precise") in got

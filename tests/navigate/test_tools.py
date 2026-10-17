import subprocess
from pathlib import Path

import pytest

from ai_usage_kit.navigate import tools as tools_mod
from ai_usage_kit.navigate.graph.config import Config, RepoConfig
from ai_usage_kit.navigate.graph.engine.index import Index
from ai_usage_kit.navigate.tools import Freshness, ToolError, Tools, split_targets
from tests.navigate.conftest import World


def _tools(world: World) -> Tools:
    return Tools(Index(world.cfg).build(), world.cfg, show_repo=True)


def _one_repo(world: World, name: str = "sharedlib") -> Tools:
    cfg = Config(repos=[RepoConfig(name, world.root / name)])
    return Tools(Index(cfg).build(), cfg, show_repo=False)


def _diff(world: World) -> str:
    done = subprocess.run(
        ["git", "-C", str(world.root / "sharedlib"), "diff", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


# ---------------------------------------------------------------- answer contract


def test_who_uses_text_shape(world: World) -> None:
    out = _tools(world).who_uses("sharedlib.core.price")
    assert "who_uses sharedlib.core.price" in out
    assert "stale" not in out
    assert "agent_client (" in out and "svc_api (" in out  # grouped by repo
    assert "svc_api:src/svc_api/routes.py:12  precise call" in out  # repo:path:line + conf + kind + snippet
    assert "price(order)" in out


def test_ambiguity_is_reported_with_qualified_names(world: World) -> None:
    out = _tools(world).who_uses("parse")
    assert "ambiguous" in out and "sharedlib.util.parse" in out and "svc_api.util.parse" in out


def test_unknown_symbol_suggests(world: World) -> None:
    out = _tools(world).who_uses("parse_ordr")
    assert "not found" in out and "sharedlib.core.parse_order" in out and "try Grep" in out


def test_kinds_filter(world: World) -> None:
    out = _tools(world).who_uses("sharedlib.core.price", kinds=["call"])
    assert "import" not in out.split("== ", 1)[1]


def test_paging_at_40(world: World) -> None:
    body = "from sharedlib import price\n\n\n" + "".join(
        f"def f{i}(o):\n    return price(o)\n\n\n" for i in range(60)
    )
    (world.root / "svc_api" / "src" / "svc_api" / "many.py").write_text(body, encoding="utf-8")
    tools = _tools(world)
    res = tools.idx.resolve_target("sharedlib.core.price")
    total = len(tools.idx.uses(res.candidates[0], {"call"}))
    first = tools.who_uses("sharedlib.core.price", kinds=["call"])
    assert first.count(" call  ") == 40
    assert "more (add --page 2)" in first
    second = tools.who_uses("sharedlib.core.price", kinds=["call"], page=2)
    assert second.count(" call  ") == total - 40 and "more (add --page" not in second


def test_impact_symbol_lists_files_and_tests(world: World) -> None:
    out = _tools(world).impact(symbol="sharedlib.core.price")
    assert "affected: 3 repos" in out
    assert "src/svc_api/routes.py - L2 import/precise, L12 call/precise" in out
    assert "tests to run" in out
    assert "sharedlib:tests/test_core.py - " in out
    assert "agent_client:tests/test_client.py - imports" in out


def test_impact_route(world: World) -> None:
    out = _tools(world).impact(symbol="GET /api/v1/orders/{id}")
    assert "agent_client/client.py - L16 http/http" in out
    assert "svc_api:tests/test_app.py" in out


def test_impact_requires_exactly_one(world: World) -> None:
    tools = _tools(world)
    with pytest.raises(ToolError):
        tools.impact()
    with pytest.raises(ToolError):
        tools.impact(symbol="a", diff="b")


def test_impact_of_uncommitted_diff(world: World) -> None:
    f = world.root / "sharedlib" / "src" / "sharedlib" / "core.py"
    f.write_text(
        f.read_text().replace(
            "return round(order.amount * (1 + tax), 2)", "return round(order.amount * (1 + tax) + 0.01, 2)"
        )
    )
    out = _tools(world).impact(diff=_diff(world))
    assert "repo: sharedlib" in out
    assert "sharedlib.core.price" in out.split("changed:")[1].splitlines()[0]
    assert "src/svc_api/routes.py" in out and "agent_client/client.py" in out
    assert "sharedlib.core.Pricer.quote" not in out.split("changed:")[1].splitlines()[0]


def test_impact_diff_with_only_additions_changes_nothing(world: World) -> None:
    f = world.root / "sharedlib" / "src" / "sharedlib" / "core.py"
    f.write_text(f.read_text() + "\n\ndef brand_new():\n    return 1\n")
    assert "no existing symbol changed" in _tools(world).impact(diff=_diff(world))


def test_impact_diff_method_body_insert_maps_to_method(world: World) -> None:
    f = world.root / "sharedlib" / "src" / "sharedlib" / "core.py"
    f.write_text(
        f.read_text().replace(
            "        return round(value, 2)\n", "        value = abs(value)\n        return round(value, 2)\n"
        )
    )
    assert "sharedlib.core.Pricer._round" in _tools(world).impact(diff=_diff(world))


def test_unparsable_diff_is_a_tool_error(world: World) -> None:
    with pytest.raises(ToolError):
        _tools(world).impact(diff="not a diff")


# ---------------------------------------------------------------- where / outline


def test_where_gives_span(world: World) -> None:
    out = _tools(world).where("sharedlib.core.price")
    assert "sharedlib:src/sharedlib/core.py:12-13  function  sharedlib.core.price" in out
    assert "offset/limit" in out


def test_where_reexported_name(world: World) -> None:
    assert "src/sharedlib/core.py:12-13" in _tools(world).where("sharedlib.price")


def test_where_ambiguous_lists_all(world: World) -> None:
    out = _tools(world).where("parse")
    assert "2 definitions match" in out
    assert "sharedlib.util.parse" in out and "svc_api.util.parse" in out


def test_where_unknown(world: World) -> None:
    out = _tools(world).where("parse_ordr")
    assert "not found" in out and "sharedlib.core.parse_order" in out


def test_where_requires_symbol(world: World) -> None:
    with pytest.raises(ToolError):
        _tools(world).where("  ")


def test_outline_lists_definitions_with_nesting(world: World) -> None:
    out = _tools(world).outline("src/sharedlib/core.py")
    lines = out.splitlines()
    assert "8-9  function  parse_order" in lines
    assert "12-13  function  price" in lines
    assert "16-27  class  Pricer" in lines
    assert "  20-21  method  quote" in lines


def test_outline_accepts_absolute_and_backslash_paths(world: World) -> None:
    tools = _tools(world)
    expected = tools.outline("src/sharedlib/core.py").splitlines()[1:]
    absolute = str(world.root / "sharedlib" / "src" / "sharedlib" / "core.py")
    assert tools.outline(absolute).splitlines()[1:] == expected
    assert tools.outline("src\\sharedlib\\core.py").splitlines()[1:] == expected
    assert tools.outline("./src/sharedlib/core.py").splitlines()[1:] == expected


def test_outline_unknown_path(world: World) -> None:
    assert "not indexed: nope.py" in _tools(world).outline("nope.py")


# ---------------------------------------------------------------- single repo, freshness, cap


def test_single_repo_prints_plain_paths(world: World) -> None:
    tools = _one_repo(world)
    out = tools.who_uses("sharedlib.core.price")
    assert "tests/test_core.py:11  precise call" in out
    assert "sharedlib:" not in out
    assert "src/sharedlib/core.py:12-13  function" in tools.where("sharedlib.core.price")
    assert "sharedlib:" not in tools.impact(symbol="sharedlib.core.price")


def test_stale_line_lists_changed_files(world: World) -> None:
    tools = _one_repo(world)
    tools.freshness = lambda: Freshness("abc1234", True, ["a.py", "b.py"])
    for out in (
        tools.where("price"),
        tools.who_uses("price"),
        tools.impact(symbol="price"),
        tools.outline("x.py"),
    ):
        assert "stale: true (graph built at abc1234); changed since: a.py, b.py; read those directly" in out


def test_stale_line_truncates_at_20(world: World) -> None:
    tools = _one_repo(world)
    tools.freshness = lambda: Freshness("abc1234", True, [f"f{i}.py" for i in range(25)])
    out = tools.where("price")
    assert "f19.py" in out and "f20.py" not in out and "... 5 more" in out


def test_fresh_graph_prints_no_freshness_line(world: World) -> None:
    tools = _one_repo(world)
    tools.freshness = lambda: Freshness("abc1234", False, [])
    assert "stale" not in tools.where("price")


def test_output_is_capped(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod, "MAX_CHARS", 400)
    out = _tools(world).who_uses("sharedlib.core.price")
    assert len(out) <= 400
    assert out.endswith("... output cut at 400 characters; add --page 2 or ask for a narrower symbol")


# ---------------------------------------------------------------- several names, patterns, depth


def test_split_targets() -> None:
    assert split_targets("a|b| a ||c") == ["a", "b", "c"]
    assert split_targets("  ") == []


def test_several_names_in_one_call(world: World) -> None:
    out = _tools(world).where("sharedlib.core.price|sharedlib.core.parse_order")
    assert "== sharedlib.core.price" in out and "== sharedlib.core.parse_order" in out
    assert "core.py:12-13" in out and "core.py:8-9" in out


def test_duplicate_names_answer_once(world: World) -> None:
    tools = _tools(world)
    assert tools.where("sharedlib.core.price|sharedlib.core.price") == tools.where("sharedlib.core.price")


def test_more_than_ten_names(world: World) -> None:
    out = _tools(world).where("|".join(f"n{i}" for i in range(12)))
    assert "== n9" in out and "== n10" not in out
    assert "... 2 more names not answered; ask again with the rest" in out


def test_who_uses_several_names(world: World) -> None:
    out = _tools(world).who_uses("sharedlib.core.price|sharedlib.core.Pricer")
    assert "== sharedlib.core.price" in out and "== sharedlib.core.Pricer" in out
    assert "agent_client/client.py:29" in out  # subclass of Pricer


def test_where_wildcard(world: World) -> None:
    out = _tools(world).where("*parse*")
    for name in ("sharedlib.core.parse_order", "sharedlib.util.parse", "svc_api.util.parse"):
        assert name in out


def test_who_uses_wildcard(world: World) -> None:
    out = _tools(world).who_uses("sharedlib.core.pri*")
    assert "svc_api:src/svc_api/routes.py:12  precise call" in out


def test_wildcard_without_match(world: World) -> None:
    assert "not found" in _tools(world).where("zz*zz")


def test_depth_two_shows_callers_of_callers(world: World) -> None:
    out = _tools(world).who_uses("sharedlib.core.Pricer._round", depth=2)
    lines = out.splitlines()
    first = next(i for i, line in enumerate(lines) if "src/sharedlib/core.py:21" in line)
    deeper = [line for line in lines[first + 1 :] if "via quote" in line]
    assert deeper and all(line.startswith("    ") for line in deeper)
    assert any("agent_client/client.py:26" in line for line in deeper)


def test_depth_terminates_on_cycles(world: World) -> None:
    loop = "def a():\n    return b()\n\n\ndef b():\n    return a()\n"
    (world.root / "svc_api" / "src" / "svc_api" / "loop.py").write_text(loop, encoding="utf-8")
    out = _tools(world).who_uses("svc_api.loop.a", depth=3)
    assert out.count("loop.py:6") == 1 and out.count("loop.py:2") == 1


@pytest.mark.parametrize("depth", [0, 4])
def test_depth_out_of_range(world: World, depth: int) -> None:
    with pytest.raises(ToolError):
        _tools(world).who_uses("price", depth=depth)


def test_page_must_be_positive(world: World) -> None:
    with pytest.raises(ToolError):
        _tools(world).who_uses("price", page=0)


def test_unknown_kind_is_rejected(world: World) -> None:
    with pytest.raises(ToolError):
        _tools(world).who_uses("price", kinds=["calls"])


def test_paths_are_relative(world: World) -> None:
    out = _one_repo(world).where("price")
    assert str(Path(world.root)) not in out


def test_output_is_ascii_apart_from_code(world: World) -> None:
    tools = _tools(world)
    for out in (
        tools.who_uses("sharedlib.core.price"),
        tools.impact(symbol="sharedlib.core.price"),
        tools.where("parse"),
        tools.who_uses("parse"),
    ):
        assert out.isascii(), [c for c in out if not c.isascii()]


def test_absolute_path_with_other_letter_case(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod, "CASE_INSENSITIVE_PATHS", True)
    tools = _tools(world)
    expected = tools.outline("src/sharedlib/core.py").splitlines()[1:]
    absolute = str(world.root / "sharedlib" / "src" / "sharedlib" / "core.py").swapcase()
    assert tools.outline(absolute).splitlines()[1:] == expected

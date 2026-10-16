"""uv and npm cases ported from the reference tool (see THIRD_PARTY_NOTICES.md), plus our npm WARN handling,
which differs on purpose: warnings are kept (deduplicated, capped) instead of dropped."""

from ai_usage_kit.trim.filters.npm import filter_npm
from ai_usage_kit.trim.filters.uv import filter_uv_install


def test_uv_audited_short_circuits() -> None:
    output = "Resolved 42 packages in 123ms\nAudited 42 packages in 0.05ms\n"
    assert filter_uv_install(output) == "ok (up to date)"


def test_uv_strips_download_and_cached_lines() -> None:
    output = """  Downloading requests-2.31.0-py3-none-any.whl (62.6 kB)
  Using cached certifi-2023.11.17-py3-none-any.whl (162 kB)
  Preparing packages...
Installed 5 packages in 23ms
 + certifi==2023.11.17
 + charset-normalizer==3.3.2
 + idna==3.6
 + requests==2.31.0
 + urllib3==2.1.0
"""
    assert filter_uv_install(output) == (
        "Installed 5 packages in 23ms\n + certifi==2023.11.17\n + charset-normalizer==3.3.2\n"
        " + idna==3.6\n + requests==2.31.0\n + urllib3==2.1.0"
    )


def test_uv_caps_at_twenty_lines() -> None:
    output = "Installed 30 packages in 1s\n" + "".join(f" + pkg{i}==1.0\n" for i in range(30))
    lines = filter_uv_install(output).splitlines()
    assert len(lines) == 21
    assert lines[-1] == "... (11 lines truncated)"


def test_npm_strips_banners_and_notices_keeps_build_lines() -> None:
    output = """
> project@1.0.0 build
> next build

npm WARN deprecated inflight@1.0.6: This module is not supported
npm notice

   Creating an optimized production build...
   \u2713 Build completed
"""
    result = filter_npm(output)
    assert "npm notice" not in result
    assert "> project@" not in result
    assert "Build completed" in result


def test_npm_empty_is_ok() -> None:
    assert filter_npm("\n\n\n") == "ok"


# Our deliberate difference: npm WARN lines are kept, deduplicated and capped.
def test_npm_warn_lines_kept_deduplicated_and_capped() -> None:
    warns = "".join(f"npm WARN deprecated pkg{i}@1.0.0: old\n" for i in range(25))
    output = "npm WARN deprecated pkg0@1.0.0: old\n" + warns + "added 300 packages in 9s\n"
    result = filter_npm(output)
    assert result.count("npm WARN deprecated pkg0@1.0.0: old") == 1
    assert "npm WARN deprecated pkg19@1.0.0" in result
    assert "npm WARN deprecated pkg20@1.0.0" not in result
    assert "... (5 more npm WARN lines in the full output)" in result
    assert "added 300 packages in 9s" in result


def test_npm_progress_glyphs_dropped() -> None:
    assert filter_npm("\u2e28####\u2e29 reify\n...\nadded 1 package\n") == "added 1 package"

"""Is the published graph still current? Asked of git, which answers in milliseconds even for large repos.

Stale when HEAD moved since the build, or when the set of uncommitted Python changes (`git status`) or
the size or modification time of one of them differs from what it was at build time. The changed files are
listed: the uncommitted ones that differ, plus, when HEAD moved, the Python files that differ between the two
commits.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_usage_kit.navigate import files
from ai_usage_kit.navigate.tools import Freshness

_ABSENT = object()


def check(root: Path, meta: Mapping[str, Any]) -> Freshness:
    built_head = meta.get("head")
    indexed = built_head[:7] if isinstance(built_head, str) and built_head else None
    found = files.status(root)  # HEAD and the uncommitted Python changes, in one git call
    if found is None:
        return Freshness(indexed, True, [])
    head, dirty = found
    recorded = meta.get("dirty")
    before: Mapping[str, Any] = recorded if isinstance(recorded, dict) else {}
    changed = {k for k in set(dirty) | set(before) if dirty.get(k, _ABSENT) != before.get(k, _ABSENT)}
    if head != built_head and isinstance(built_head, str) and head:
        between = files.changed_between(root, built_head, head)
        changed |= set(between or [])
    return Freshness(indexed, head != built_head or bool(changed), sorted(changed))

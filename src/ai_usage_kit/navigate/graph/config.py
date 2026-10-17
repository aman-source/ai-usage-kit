"""Repository configuration for the engine: which repositories, which files are never read, and a git helper.

`git()` decodes output as UTF-8 with replacement.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PAGE_SIZE = 40
SKIP_DIRS = {
    ".git",
    ".hg",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    ".nox",
    "site-packages",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".eggs",
}
# Never read, index or print these.
DENY_GLOBS = (
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*.token",
    "credentials*",
    ".netrc",
    "*.kdbx",
    "*secret*",
    "*credential*",
)
NON_OWNER_TOPS = {
    "tests",
    "test",
    "docs",
    "doc",
    "examples",
    "example",
    "scripts",
    "benchmarks",
    "setup",
    "conftest",
    "noxfile",
    "tools",
    "site",
    "build",
}


def denied(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), g) for g in DENY_GLOBS)


@dataclass
class RepoConfig:
    name: str
    path: Path
    packages: list[str] = field(default_factory=list)  # extra top-level names this repo owns
    source_roots: list[str] = field(default_factory=list)  # default: ["src", "."] if src/ exists
    route_prefix: str = ""  # mount prefix applied to this repo's routes
    commit: str | None = None  # pinned commit (informational / checks)

    def roots(self) -> list[str]:
        if self.source_roots:
            return self.source_roots
        return ["src", "."] if (self.path / "src").is_dir() else ["."]


@dataclass
class Config:
    repos: list[RepoConfig]
    page_size: int = PAGE_SIZE

    def repo(self, name: str) -> RepoConfig | None:
        return next((r for r in self.repos if r.name == name), None)


def git(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def head_commit(path: Path) -> str | None:
    return git(path, "rev-parse", "HEAD")


def is_dirty(path: Path) -> bool:
    out = git(path, "status", "--porcelain", "--untracked-files=no")
    return bool(out)


def iter_files(root: Path, suffix: str | None = None):
    """Walk a checkout, pruning vendored dirs and secret files."""
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for fn in sorted(fns):
            if denied(fn) or (suffix and not fn.endswith(suffix)):
                continue
            yield Path(dp) / fn

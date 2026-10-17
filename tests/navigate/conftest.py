"""Fixture repos for navigate: copied to a temp dir, each its own git repo."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_usage_kit.navigate.graph.config import Config, RepoConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=GIT_ENV
    )
    return done.stdout.strip()


def init_repo(path: Path) -> None:
    git(path, "init", "-q")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")


@dataclass
class World:
    root: Path
    tmp: Path
    cfg: Config


@pytest.fixture()
def world(tmp_path: Path) -> World:
    """Three fixture repos copied to tmp, each its own git repo, and a Config listing them."""
    root = tmp_path / "world"
    shutil.copytree(FIXTURES / "repos", root)
    for repo in root.iterdir():
        init_repo(repo)
    cfg = Config(repos=[RepoConfig(r.name, r) for r in sorted(root.iterdir())])
    return World(root, tmp_path, cfg)

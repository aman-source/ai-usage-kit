import pytest

from ai_usage_kit.trim.matching import match_command


@pytest.mark.parametrize(
    ("command", "name"),
    [
        ("pytest", "pytest"),
        ("pytest -v tests/", "pytest"),
        ("python -m pytest -q", "pytest"),
        ("python3 -m pytest tests", "pytest"),
        ("uv run pytest -v", "pytest"),
        ("pytest.exe -v", "pytest"),
        ("uv sync", "uv"),
        ("uv sync --all-extras", "uv"),
        ("uv pip install -r requirements.txt", "uv"),
        ("npm install", "npm"),
        ("npm i", "npm"),
        ("npm ci", "npm"),
        ("npm run build", "npm"),
        ("./gradlew assembleDebug", "gradle"),
        ("gradle clean build", "gradle"),
        ("gradlew.bat build", "gradle"),
    ],
)
def test_qualifying_commands(command: str, name: str) -> None:
    match = match_command(command)
    assert match is not None and match.name == name


@pytest.mark.parametrize(
    "command",
    [
        "pytest -v | tail -5",
        "pytest && echo ok",
        "pytest; ls",
        "pytest > out.txt",
        "pytest $(cat args)",
        "pytest `cat args`",
        "cd x && npm install && npm test",
        "pytest -s",
        "pytest --capture=no",
        "pytest --collect-only",
        "pytest --co",
        "pytest --durations=10",
        "pytest -rA",
        "pytest -rP",
        "pytest --version",
        "pytest -h",
        "pytest --fixtures",
        "pytest --setup-show",
        "pytest --pdb",
        "pip install requests",
        "uv run python app.py",
        "uv add requests",
        "npm test",
        "npm view react",
        "./gradlew test",
        "./gradlew assembleDebug --stacktrace",
        "gradle signingReport",
        "git diff",
        "git status",
        "ls -la",
        "pytest 'unterminated",
        "",
    ],
)
def test_non_qualifying_commands(command: str) -> None:
    assert match_command(command) is None


@pytest.mark.parametrize(
    ("command", "name"),
    [
        ("cd src && pytest -q", "pytest"),
        ('cd "C:/work/my repo" && uv run pytest tests', "pytest"),
        ("cd 'my dir' && npm ci", "npm"),
        ("cd /repo/app  &&  ./gradlew build", "gradle"),
    ],
)
def test_one_leading_cd_is_allowed(command: str, name: str) -> None:
    found = match_command(command)
    assert found is not None and found.name == name


@pytest.mark.parametrize(
    "command",
    [
        "cd src && pytest -q | tail",
        "cd src && cd tests && pytest",
        "cd src; pytest",
        "cd src || pytest",
        "cd $(mktemp -d) && pytest",
        'cd "$HOME" && pytest',
        "cd src && pytest -s",
        "cd src && git status",
        "cd && pytest",
        "pytest && cd src",
    ],
)
def test_other_chains_with_cd_are_not_trimmed(command: str) -> None:
    assert match_command(command) is None

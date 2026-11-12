import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from repolens.ingest.clone import (
    CloneError,
    CloneTooLargeError,
    GitHubRepository,
    InvalidRepositoryError,
    parse_github_url,
    shallow_clone,
)
from tests.ingest.git_fixtures import git, make_repo

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route temporary directories here so tests can check that clones are removed."""
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/app", GitHubRepository("acme", "app")),
        ("https://github.com/acme/app.git", GitHubRepository("acme", "app")),
        ("https://github.com/acme/app/", GitHubRepository("acme", "app")),
        ("  https://github.com/Acme-Co/my.repo_2 ", GitHubRepository("Acme-Co", "my.repo_2")),
    ],
)
def test_github_urls_are_accepted(url: str, expected: GitHubRepository) -> None:
    assert parse_github_url(url) == expected
    assert expected.url == f"https://github.com/{expected.owner}/{expected.name}"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/app",
        "git@github.com:acme/app.git",
        "ssh://git@github.com/acme/app",
        "file:///etc",
        "https://gitlab.com/acme/app",
        "https://github.com.evil.com/acme/app",
        "https://evil.com/github.com/acme/app",
        "https://user:token@github.com/acme/app",
        "https://github.com:443/acme/app",
        "https://github.com/acme",
        "https://github.com/acme/app/tree/main",
        "https://github.com/acme/app?x=1",
        "https://github.com/acme/app#readme",
        "https://github.com/acme/..",
        "https://github.com/-acme/app",
        "https://github.com/acme/app\n--upload-pack=touch",
        "",
    ],
)
def test_other_urls_are_rejected(url: str) -> None:
    with pytest.raises(InvalidRepositoryError):
        parse_github_url(url)


async def test_clone_checks_out_the_default_branch_and_reports_the_commit(
    tmp_path: Path, scratch: Path
) -> None:
    source = make_repo(tmp_path / "origin", {"src/main.py": "x = 1\n"})
    head = git(tmp_path / "origin", "rev-parse", "HEAD")

    async with shallow_clone(source) as checkout:
        assert (checkout.root / "src" / "main.py").read_text() == "x = 1\n"
        assert checkout.commit_sha == head
        assert git(checkout.root, "rev-parse", "--is-shallow-repository") == "true"

    assert list(scratch.iterdir()) == []


async def test_clone_checks_out_a_named_branch(tmp_path: Path, scratch: Path) -> None:
    origin = tmp_path / "origin"
    source = make_repo(origin, {"a.py": "main\n"})
    git(origin, "switch", "--quiet", "--create", "feature/x")
    (origin / "a.py").write_text("feature\n")
    git(origin, "commit", "--quiet", "--all", "--message=feature")

    async with shallow_clone(source, ref="feature/x") as checkout:
        assert (checkout.root / "a.py").read_text() == "feature\n"


@pytest.mark.parametrize("ref", ["-u", "--upload-pack=touch", "a..b", "", "main branch"])
async def test_unsafe_refs_are_rejected(tmp_path: Path, ref: str) -> None:
    source = make_repo(tmp_path / "origin", {"a.py": ""})

    with pytest.raises(InvalidRepositoryError):
        async with shallow_clone(source, ref=ref):
            pass


@pytest.mark.parametrize("source", ["ext::sh -c touch% /tmp/pwned", "ssh://host/repo", "/srv/repo"])
async def test_only_https_and_file_sources_are_cloned(source: str) -> None:
    with pytest.raises(InvalidRepositoryError):
        async with shallow_clone(source):
            pass


async def test_clone_over_the_size_limit_is_stopped_and_removed(
    tmp_path: Path, scratch: Path
) -> None:
    source = make_repo(tmp_path / "origin", {"blob.bin": os.urandom(300_000)})

    with pytest.raises(CloneTooLargeError):
        async with shallow_clone(source, max_bytes=100_000):
            pytest.fail("an oversized clone must not be yielded")

    assert list(scratch.iterdir()) == []


async def test_failed_clone_is_removed(tmp_path: Path, scratch: Path) -> None:
    with pytest.raises(CloneError, match="git clone failed"):
        async with shallow_clone((tmp_path / "missing").as_uri()):
            pass

    assert list(scratch.iterdir()) == []


async def test_clone_is_removed_when_the_body_fails(tmp_path: Path, scratch: Path) -> None:
    source = make_repo(tmp_path / "origin", {"a.py": ""})

    with pytest.raises(RuntimeError, match="boom"):
        async with shallow_clone(source):
            raise RuntimeError("boom")

    assert list(scratch.iterdir()) == []


async def test_no_hooks_or_filters_run_during_clone(
    tmp_path: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "executed"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for hook in ("post-checkout", "reference-transaction"):
        script = hooks / hook
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        script.chmod(0o755)
    templates = tmp_path / "templates"
    (templates / "hooks").mkdir(parents=True)
    for hook in hooks.iterdir():
        (templates / "hooks" / hook.name).write_bytes(hook.read_bytes())
        (templates / "hooks" / hook.name).chmod(0o755)
    global_config = tmp_path / "gitconfig"
    global_config.write_text(
        f"[core]\n\thooksPath = {hooks}\n"
        f"[filter \"evil\"]\n\tsmudge = touch '{marker}'; cat\n"
        f"[init]\n\ttemplateDir = {templates}\n"
    )
    source = make_repo(
        tmp_path / "origin", {".gitattributes": "* filter=evil\n", "main.py": "x = 1\n"}
    )
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(templates))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

    # Control: a plain clone in this environment does run them.
    subprocess.run(["git", "clone", "--quiet", source, str(tmp_path / "plain")], check=True)
    assert marker.exists()
    marker.unlink()

    async with shallow_clone(source) as checkout:
        assert (checkout.root / "main.py").read_text() == "x = 1\n"

    assert not marker.exists()

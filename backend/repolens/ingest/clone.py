"""Validate GitHub URLs and shallow-clone repositories without running any of their code.

Git runs with no system or user config, no inherited ``GIT_*`` variables, no hook templates and
only the source's protocol enabled, so neither the repository nor this machine's git setup can
add hooks, filters or other commands to a clone.
"""

import asyncio
import os
import re
import tempfile
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_CLONE_BYTES = 200 * 1024 * 1024
CLONE_TIMEOUT_SECONDS = 300.0
_POLL_SECONDS = 0.2

_GITHUB_URL = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})"
    r"/(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?"
)
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
# GitHub clones use https; local file sources exist for tests. Never ext:: or ssh.
_SCHEMES = frozenset({"https", "file"})


class InvalidRepositoryError(ValueError):
    """The URL or ref is not one RepoLens accepts."""


class CloneError(Exception):
    """git could not clone the repository."""


class CloneTooLargeError(CloneError):
    """The clone grew past the size limit and was stopped."""


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class Checkout:
    root: Path
    commit_sha: str


def parse_github_url(url: str) -> GitHubRepository:
    """Accept only ``https://github.com/<owner>/<repo>``, with an optional ``.git`` or ``/``."""
    match = _GITHUB_URL.fullmatch(url.strip())
    if match is None or match["name"] in {".", ".."}:
        raise InvalidRepositoryError("only https://github.com/<owner>/<repo> URLs are accepted")
    return GitHubRepository(owner=match["owner"], name=match["name"])


def validate_ref(ref: str) -> None:
    """Accept branch and tag names only: nothing git could read as an option or a range."""
    if _REF.fullmatch(ref) is None or ".." in ref:
        raise InvalidRepositoryError(f"invalid ref: {ref!r}")


def clone_github(
    repository: GitHubRepository, ref: str | None, *, max_bytes: int = MAX_CLONE_BYTES
) -> AbstractAsyncContextManager[Checkout]:
    return shallow_clone(repository.url, ref=ref, max_bytes=max_bytes)


@asynccontextmanager
async def shallow_clone(
    source: str,
    *,
    ref: str | None = None,
    max_bytes: int = MAX_CLONE_BYTES,
    timeout: float = CLONE_TIMEOUT_SECONDS,
) -> AsyncIterator[Checkout]:
    """Clone ``source`` at ``ref`` (a branch or tag; default branch if None) into a temporary
    directory that is removed when the context exits, whether or not the body succeeded.
    """
    if urlsplit(source).scheme not in _SCHEMES:
        raise InvalidRepositoryError("only https and file sources can be cloned")
    if ref is not None:
        validate_ref(ref)
    with tempfile.TemporaryDirectory(prefix="repolens-clone-") as scratch:
        root = Path(scratch) / "repo"
        await _clone(source, root, ref=ref, max_bytes=max_bytes, timeout=timeout)
        commit_sha = await _head_commit(root)
        yield Checkout(root=root, commit_sha=commit_sha)


async def _clone(
    source: str, root: Path, *, ref: str | None, max_bytes: int, timeout: float
) -> None:
    scheme = urlsplit(source).scheme
    branch = ["--branch", ref] if ref is not None else []
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "protocol.allow=never",
        "-c",
        f"protocol.{scheme}.allow=always",
        "-c",
        f"core.hooksPath={os.devnull}",
        "clone",
        "--depth=1",
        "--single-branch",
        "--no-tags",
        "--template=",
        *branch,
        "--",
        source,
        str(root),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=_git_env(),
    )
    assert process.stderr is not None
    stderr = asyncio.create_task(process.stderr.read())
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            try:
                await asyncio.wait_for(process.wait(), _POLL_SECONDS)
                break
            except TimeoutError:
                pass
            if _tree_size(root) > max_bytes:
                raise CloneTooLargeError(f"repository is larger than {max_bytes} bytes")
            if asyncio.get_running_loop().time() > deadline:
                raise CloneError(f"clone took longer than {timeout:g} seconds")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        message = (await stderr).decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        detail = message.splitlines()[-1] if message else f"exit code {process.returncode}"
        raise CloneError(f"git clone failed: {detail}")
    if _tree_size(root) > max_bytes:
        raise CloneTooLargeError(f"repository is larger than {max_bytes} bytes")


async def _head_commit(root: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(root),
        "-c",
        "core.fsmonitor=false",
        "rev-parse",
        "HEAD",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_git_env(),
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        raise CloneError("could not read the cloned commit")
    return output.decode().strip()


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return env | {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }


def _tree_size(root: Path) -> int:
    total = 0
    for directory, _, files in os.walk(root):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except FileNotFoundError:
                continue  # git removes temporary files while it runs
    return total

"""Build local git repositories for clone tests, isolated from this machine's git config."""

import os
import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env |= {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    identity = ["-c", "user.name=RepoLens Tests", "-c", "user.email=tests@example.invalid"]
    result = subprocess.run(
        ["git", *identity, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def make_repo(root: Path, files: dict[str, str | bytes]) -> str:
    """Create a repository at ``root`` with one commit holding ``files``; return its file URL."""
    root.mkdir(parents=True)
    git(root, "init", "--quiet", "--initial-branch=main")
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--message=initial")
    return root.as_uri()

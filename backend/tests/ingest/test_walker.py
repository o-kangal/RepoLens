import os
from pathlib import Path

import pytest

from repolens.ingest.walker import (
    FileLimitExceededError,
    Language,
    SourceFile,
    detect_language,
    walk_repository,
)


def write(root: Path, rel_path: str, content: str | bytes = "x = 1\n") -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def paths(files: list[SourceFile]) -> list[str]:
    return [source.path for source in files]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("src/app.ts", Language.TYPESCRIPT),
        ("src/App.tsx", Language.TYPESCRIPT),
        ("lib/index.mts", Language.TYPESCRIPT),
        ("lib/index.js", Language.JAVASCRIPT),
        ("lib/view.jsx", Language.JAVASCRIPT),
        ("lib/config.cjs", Language.JAVASCRIPT),
        ("pkg/main.py", Language.PYTHON),
        ("pkg/stubs.pyi", Language.PYTHON),
        ("README.md", Language.MARKDOWN),
        ("docs/GUIDE.MD", Language.MARKDOWN),
        ("Cargo.toml", None),
        ("Makefile", None),
        ("image.png", None),
    ],
)
def test_detect_language(path: str, language: Language | None) -> None:
    assert detect_language(path) == language


def test_walk_returns_supported_files_sorted_with_text(repo: Path) -> None:
    write(repo, "src/b.ts", "export const b = 2;\n")
    write(repo, "src/a.py", "a = 1\n")
    write(repo, "README.md", "# Title\n")
    write(repo, "package.json", "{}\n")

    assert walk_repository(repo) == [
        SourceFile("README.md", Language.MARKDOWN, "# Title\n"),
        SourceFile("src/a.py", Language.PYTHON, "a = 1\n"),
        SourceFile("src/b.ts", Language.TYPESCRIPT, "export const b = 2;\n"),
    ]


def test_walk_respects_root_gitignore(repo: Path) -> None:
    write(repo, ".gitignore", "node_modules/\n*.gen.ts\n!keep.gen.ts\n/dist\n")
    write(repo, "node_modules/lib/index.js")
    write(repo, "src/api.gen.ts")
    write(repo, "src/keep.gen.ts")
    write(repo, "dist/bundle.js")
    write(repo, "src/dist/kept.js")
    write(repo, "src/main.ts")

    assert paths(walk_repository(repo)) == ["src/dist/kept.js", "src/keep.gen.ts", "src/main.ts"]


def test_nested_gitignore_applies_below_its_directory_and_overrides_parent(repo: Path) -> None:
    write(repo, ".gitignore", "*.log.md\nfixtures.py\n")
    write(repo, "pkg/.gitignore", "local.py\n!fixtures.py\n")
    write(repo, "pkg/local.py")
    write(repo, "pkg/fixtures.py")
    write(repo, "local.py")
    write(repo, "fixtures.py")
    write(repo, "pkg/run.log.md")

    assert paths(walk_repository(repo)) == ["local.py", "pkg/fixtures.py"]


def test_files_inside_an_ignored_directory_cannot_be_reincluded(repo: Path) -> None:
    write(repo, ".gitignore", "build/\n!build/keep.py\n")
    write(repo, "build/keep.py")

    assert walk_repository(repo) == []


def test_git_directory_is_never_walked(repo: Path) -> None:
    write(repo, ".git/hooks/post-checkout.py")
    write(repo, "main.py")

    assert paths(walk_repository(repo)) == ["main.py"]


def test_binary_files_are_skipped(repo: Path) -> None:
    write(repo, "data.py", b"header\0\x01\x02payload")
    write(repo, "text.py")

    assert paths(walk_repository(repo)) == ["text.py"]


def test_non_utf8_files_are_skipped(repo: Path) -> None:
    write(repo, "latin1.py", "name = 'caf\xe9'\n".encode("latin-1"))

    assert walk_repository(repo) == []


def test_utf8_bom_is_dropped(repo: Path) -> None:
    write(repo, "bom.py", b"\xef\xbb\xbfx = 1\n")

    assert walk_repository(repo)[0].text == "x = 1\n"


def test_files_over_the_size_limit_are_skipped(repo: Path) -> None:
    write(repo, "at_limit.py", b"#" * 100)
    write(repo, "over_limit.py", b"#" * 101)

    assert paths(walk_repository(repo, max_file_bytes=100)) == ["at_limit.py"]


def test_default_size_limit_is_one_mebibyte(repo: Path) -> None:
    write(repo, "big.md", b"a" * (1024 * 1024 + 1))
    write(repo, "ok.md", b"a" * (1024 * 1024))

    assert paths(walk_repository(repo)) == ["ok.md"]


def test_file_count_limit_is_enforced(repo: Path) -> None:
    for index in range(3):
        write(repo, f"m{index}.py")
    write(repo, "notes.txt")

    assert len(walk_repository(repo, max_files=3)) == 3
    with pytest.raises(FileLimitExceededError):
        walk_repository(repo, max_files=2)


def test_symlinks_are_never_followed(tmp_path: Path, repo: Path) -> None:
    outside = tmp_path / "outside"
    write(outside, "secret.py", "TOKEN = 'leak'\n")
    write(repo, "real/inside.py")
    (repo / "secret.py").symlink_to(outside / "secret.py")
    (repo / "linked_dir").symlink_to(outside, target_is_directory=True)
    (repo / "alias.py").symlink_to(repo / "real" / "inside.py")
    (repo / "loop").symlink_to(repo, target_is_directory=True)

    assert paths(walk_repository(repo)) == ["real/inside.py"]


def test_symlinked_gitignore_is_not_read(tmp_path: Path, repo: Path) -> None:
    write(tmp_path, "outside.gitignore", "*.py\n")
    (repo / ".gitignore").symlink_to(tmp_path / "outside.gitignore")
    write(repo, "main.py")

    assert paths(walk_repository(repo)) == ["main.py"]


def test_special_files_are_skipped(repo: Path) -> None:
    os.mkfifo(repo / "pipe.py")
    write(repo, "main.py")

    assert paths(walk_repository(repo)) == ["main.py"]


def test_root_must_be_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        walk_repository(tmp_path / "missing")
    with pytest.raises(NotADirectoryError):
        walk_repository(write(tmp_path, "file.py"))

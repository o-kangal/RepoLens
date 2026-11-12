"""Walk a checked-out repository and collect the source files worth indexing.

The walker reads files only. It never runs git or anything else from the repository, never
follows symlinks, and opens nothing but regular files below the root.
"""

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 5000
_BINARY_SNIFF_BYTES = 8192


class Language(StrEnum):
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    PYTHON = "python"
    MARKDOWN = "markdown"


_LANGUAGE_BY_SUFFIX = {
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
}


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    """POSIX path relative to the repository root, as cited in answers."""
    language: Language
    text: str


class FileLimitExceededError(Exception):
    """The repository holds more indexable files than the walker accepts."""


def detect_language(path: str) -> Language | None:
    return _LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())


@dataclass(frozen=True, slots=True)
class _IgnoreRules:
    base: PurePosixPath
    spec: GitIgnoreSpec


def walk_repository(
    root: Path, *, max_files: int = MAX_FILES, max_file_bytes: int = MAX_FILE_BYTES
) -> list[SourceFile]:
    """Return the indexable files below ``root``, sorted by path.

    Raises ``FileLimitExceededError`` when more than ``max_files`` files qualify, rather than
    indexing an arbitrary subset.
    """
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)

    files: list[SourceFile] = []
    pending = [PurePosixPath()]
    rules_by_dir: dict[PurePosixPath, tuple[_IgnoreRules, ...]] = {PurePosixPath(): ()}

    while pending:
        rel_dir = pending.pop()
        abs_dir = root / rel_dir
        rules = rules_by_dir.pop(rel_dir)
        own_rules = _read_ignore_rules(abs_dir, rel_dir)
        if own_rules is not None:
            rules = (*rules, own_rules)

        with os.scandir(abs_dir) as entries:
            for entry in entries:
                rel_path = rel_dir / entry.name
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != ".git" and not _is_ignored(rules, rel_path, is_dir=True):
                        pending.append(rel_path)
                        rules_by_dir[rel_path] = rules
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                language = detect_language(entry.name)
                if language is None or _is_ignored(rules, rel_path, is_dir=False):
                    continue
                text = _read_text(Path(entry.path), max_file_bytes)
                if text is None:
                    continue
                if len(files) == max_files:
                    raise FileLimitExceededError(
                        f"repository has more than {max_files} indexable files"
                    )
                files.append(SourceFile(path=rel_path.as_posix(), language=language, text=text))

    return sorted(files, key=lambda source: source.path)


def _read_ignore_rules(abs_dir: Path, rel_dir: PurePosixPath) -> _IgnoreRules | None:
    gitignore = abs_dir / ".gitignore"
    if gitignore.is_symlink() or not gitignore.is_file():
        return None
    lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    return _IgnoreRules(base=rel_dir, spec=GitIgnoreSpec.from_lines(lines))


def _is_ignored(rules: tuple[_IgnoreRules, ...], rel_path: PurePosixPath, *, is_dir: bool) -> bool:
    # Deeper .gitignore files take precedence, so the last rule set with an opinion wins.
    for rule in reversed(rules):
        candidate = rel_path.relative_to(rule.base).as_posix() + ("/" if is_dir else "")
        include = rule.spec.check_file(candidate).include
        if include is not None:
            return include
    return False


def _read_text(path: Path, max_file_bytes: int) -> str | None:
    """Return the file as text, or None when it is too large, binary or not UTF-8."""
    with path.open("rb") as handle:
        data = handle.read(max_file_bytes + 1)
    if len(data) > max_file_bytes or b"\0" in data[:_BINARY_SNIFF_BYTES]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None

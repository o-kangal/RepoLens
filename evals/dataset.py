"""Golden dataset items, one JSON object per line, and a command that validates dataset files.

Usage: ``uv --directory backend run python ../evals/dataset.py <dataset.jsonl>...``
"""

import sys
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class QuestionType(StrEnum):
    LOCATE = "locate"
    """Where is X?"""
    EXPLAIN = "explain"
    """How does X work?"""
    MULTI_HOP = "multi-hop"
    """X calls Y, which ...: the answer spans several places."""
    DOCS = "docs"
    """How is X configured or used, answered from the documentation."""
    NEGATIVE = "negative"
    """The repository holds no answer, so the right reply is a refusal."""


class ExpectedSource(BaseModel):
    """A line range, 1-based and inclusive, that a correct retrieval must overlap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, description="Relative POSIX path inside the repository")
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def _relative_posix_path(cls, path: str) -> str:
        parts = path.split("/")
        if "\\" in path or any(part in ("", ".", "..") for part in parts):
            raise ValueError("must be a relative POSIX path inside the repository")
        return path

    @model_validator(mode="after")
    def _ordered_range(self) -> Self:
        if self.end < self.start:
            raise ValueError(f"end {self.end} is before start {self.start}")
        return self


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    type: QuestionType
    expected_sources: tuple[ExpectedSource, ...]
    reference_answer: str = Field(min_length=1)

    @model_validator(mode="after")
    def _sources_match_the_type(self) -> Self:
        if self.type is QuestionType.NEGATIVE and self.expected_sources:
            raise ValueError("a negative question has no expected sources")
        if self.type is not QuestionType.NEGATIVE and not self.expected_sources:
            raise ValueError(f"a {self.type} question needs at least one expected source")
        return self


class DatasetError(Exception):
    """A dataset file with invalid items; the message lists every problem by line."""


def parse_dataset(text: str) -> list[GoldenItem]:
    """Parse JSONL text, skipping blank lines. Raises ``DatasetError`` listing every problem."""
    items: list[GoldenItem] = []
    problems: list[str] = []
    lines: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = GoldenItem.model_validate_json(line)
        except ValidationError as exc:
            for error in exc.errors(include_url=False):
                location = ".".join(str(part) for part in error["loc"])
                problems.append(f"line {number}: {location or 'item'}: {error['msg']}")
            continue
        if item.id in lines:
            problems.append(f"line {number}: id {item.id!r} repeats line {lines[item.id]}")
            continue
        lines[item.id] = number
        items.append(item)
    if problems:
        raise DatasetError("\n".join(problems))
    return items


def load_dataset(path: Path) -> list[GoldenItem]:
    return parse_dataset(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: dataset.py <dataset.jsonl>...", file=sys.stderr)
        return 2
    failed = False
    for name in argv:
        try:
            items = load_dataset(Path(name))
        except (OSError, DatasetError) as exc:
            print(f"{name}: invalid\n{exc}", file=sys.stderr)
            failed = True
            continue
        counts = Counter(item.type for item in items)
        summary = ", ".join(f"{kind} {counts[kind]}" for kind in QuestionType)
        print(f"{name}: {len(items)} items ({summary})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dataset import DatasetError, GoldenItem, QuestionType, load_dataset, main, parse_dataset


def item(**overrides: object) -> dict[str, object]:
    return {
        "id": "q001",
        "question": "Where are JWT tokens verified?",
        "type": "locate",
        "expected_sources": [{"path": "src/middleware/auth.ts", "start": 12, "end": 48}],
        "reference_answer": "In verifyToken() in src/middleware/auth.ts.",
    } | overrides


def negative(**overrides: object) -> dict[str, object]:
    return (
        item(
            id="q050",
            question="Where is the GraphQL schema defined?",
            type="negative",
            expected_sources=[],
            reference_answer="Not found in the indexed code.",
        )
        | overrides
    )


def jsonl(*items: dict[str, object]) -> str:
    return "".join(json.dumps(entry) + "\n" for entry in items)


def test_the_plan_example_is_valid() -> None:
    parsed = GoldenItem.model_validate(item())

    assert parsed.type is QuestionType.LOCATE
    assert parsed.expected_sources[0].path == "src/middleware/auth.ts"


def test_all_five_question_types_are_accepted() -> None:
    assert [kind.value for kind in QuestionType] == [
        "locate",
        "explain",
        "multi-hop",
        "docs",
        "negative",
    ]
    for kind in ("locate", "explain", "multi-hop", "docs"):
        assert GoldenItem.model_validate(item(type=kind)).type == kind
    assert GoldenItem.model_validate(negative()).expected_sources == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "trivia"},
        {"type": "multi_hop"},
        {"id": ""},
        {"question": ""},
        {"reference_answer": ""},
        {"expected_sources": []},
        {"unexpected": True},
    ],
    ids=["unknown-type", "type-spelling", "id", "question", "answer", "no-sources", "extra"],
)
def test_invalid_items_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GoldenItem.model_validate(item(**overrides))


def test_a_negative_question_with_sources_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative question has no expected sources"):
        GoldenItem.model_validate(negative(expected_sources=item()["expected_sources"]))


@pytest.mark.parametrize(
    "source",
    [
        {"path": "src/a.ts", "start": 0, "end": 3},
        {"path": "src/a.ts", "start": 9, "end": 3},
        {"path": "/etc/passwd", "start": 1, "end": 1},
        {"path": "../outside.ts", "start": 1, "end": 1},
        {"path": "src/../../outside.ts", "start": 1, "end": 1},
        {"path": "./src/a.ts", "start": 1, "end": 1},
        {"path": "src\\a.ts", "start": 1, "end": 1},
        {"path": "src//a.ts", "start": 1, "end": 1},
        {"path": "src/", "start": 1, "end": 1},
        {"path": "", "start": 1, "end": 1},
        {"path": "src/a.ts", "start": 1},
    ],
    ids=[
        "zero",
        "reversed",
        "absolute",
        "parent",
        "nested-parent",
        "dot",
        "backslash",
        "double-slash",
        "trailing-slash",
        "empty",
        "no-end",
    ],
)
def test_invalid_sources_are_rejected(source: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GoldenItem.model_validate(item(expected_sources=[source]))


def test_a_single_line_source_is_valid() -> None:
    source = {"path": "README.md", "start": 7, "end": 7}

    assert GoldenItem.model_validate(item(type="docs", expected_sources=[source]))


def test_a_file_parses_in_order_and_skips_blank_lines() -> None:
    text = jsonl(item()) + "\n" + jsonl(negative())

    assert [parsed.id for parsed in parse_dataset(text)] == ["q001", "q050"]


def test_every_problem_is_reported_with_its_line() -> None:
    text = jsonl(item(), item(type="trivia", id="q002"), item(), negative(expected_sources=[]))
    text += "{not json\n"

    with pytest.raises(DatasetError) as caught:
        parse_dataset(text)

    lines = str(caught.value).splitlines()
    assert lines[0].startswith("line 2: type:")
    assert lines[1] == "line 3: id 'q001' repeats line 1"
    assert lines[2].startswith("line 5: ")
    assert len(lines) == 3


def test_the_command_summarises_valid_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "sample.jsonl"
    path.write_text(jsonl(item(), item(id="q002", type="explain"), negative()), encoding="utf-8")

    assert main([str(path)]) == 0
    assert capsys.readouterr().out == (
        f"{path}: 3 items (locate 1, explain 1, multi-hop 0, docs 0, negative 1)\n"
    )
    assert len(load_dataset(path)) == 3


def test_the_command_fails_on_invalid_or_missing_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(jsonl(item(type="trivia")), encoding="utf-8")

    assert main([str(bad), str(tmp_path / "missing.jsonl")]) == 1
    errors = capsys.readouterr().err
    assert f"{bad}: invalid\nline 1: type:" in errors
    assert "missing.jsonl: invalid" in errors
    assert main([]) == 2

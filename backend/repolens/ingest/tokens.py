"""Line splitting and an approximate, dependency-free token counter.

Counts approximate a BPE tokenizer closely enough to size chunks and context budgets. Real token
usage is read from provider responses, never from here. The count is additive over lines, so the
count of a run of lines is the sum of their counts.
"""

import math
import re

# Calibrated once against tiktoken cl100k_base and o200k_base on the TypeScript, JavaScript,
# Markdown and Python files in this repository's dependencies: the unscaled count came out at
# 1.31 times tiktoken overall (1.25 to 1.38 per language).
_SCALE = 1 / 1.31
_LETTERS_PER_TOKEN = 6

_LINE = re.compile(r"[^\n]*\n|[^\n]+")
_PIECE = re.compile(r"[A-Za-z]+|\d|[^\sA-Za-z\d]|\n")


def split_lines(text: str) -> list[str]:
    """Split on ``\\n`` only, keeping line endings, so numbering matches git and GitHub."""
    return _LINE.findall(text)


def count_tokens(text: str) -> int:
    return sum(_count_line(line) for line in split_lines(text))


def _count_line(line: str) -> int:
    units = 0
    for piece in _PIECE.findall(line):
        units += math.ceil(len(piece) / _LETTERS_PER_TOKEN) if piece[0].isalpha() else 1
    return round(units * _SCALE)

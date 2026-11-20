# Golden datasets

Each file holds one JSON object per line, in the schema of `evals/dataset.py`: an `id`, the
`question`, its `type` (`locate`, `explain`, `multi-hop`, `docs` or `negative`), the
`expected_sources` a correct retrieval must overlap (repository-relative path and 1-based,
inclusive line range; none for `negative`), and a `reference_answer`. Check a file with:

```bash
uv --directory backend run python ../evals/dataset.py ../evals/datasets/<file>.jsonl
```

## Review process

Every dataset is reviewed in rounds before it is used.

1. **Review.** Two Claude subagents each review every item against the repository at the
   pinned commit. They start without the drafting context and know nothing of each other or of
   earlier rounds. Both get the reviewer instructions below, unchanged. Each item is rated OK,
   MINOR (correct, but missing nuance or a source range) or MAJOR (a false claim, a wrong or
   missing essential source, a negative question whose feature exists, or an ambiguous
   question), with `file:line` evidence.
2. **Check.** Claude checks every MINOR and MAJOR finding in the code. Findings that hold are
   applied before the next round. The owner decides findings where the reviewers conflict (one
   rates MAJOR what the other rates OK) and findings Claude cannot confirm in the code.
3. **Repeat.** A round with a MAJOR finding is followed by a new round with two new reviewers.
4. **Stop.** The review ends after two consecutive rounds with only MINOR findings. The fixes
   from the last round are applied but not reviewed again. If a fourth round still has a MAJOR
   finding, the review stops and the remaining items go to the owner.

### Reviewer instructions

The text each reviewer receives, with the dataset path, the repository, its commit and the path
of a local checkout filled in:

> You are an independent reviewer of a golden evaluation dataset for a code-search RAG system.
> Another author drafted it; your job is to check every item against the real source code and
> report problems. Be skeptical and precise. Do not trust the reference answers; verify each
> claim yourself in the code.
>
> Inputs (read-only, do NOT modify any file anywhere):
> - Dataset: `<dataset path>`. Fields: id, question, type (locate | explain | multi-hop | docs |
>   negative), expected_sources (list of {path, start, end}; 1-based inclusive line ranges,
>   paths relative to the repo root), reference_answer.
> - Source checkout of `<repository>` at `<release>`, commit `<sha>`: `<checkout path>`. Read
>   files there. Do not run any of its code, package scripts or installs, and do not use git
>   commands that change it.
>
> Meaning of the fields:
> - The expected sources are what a good retrieval must find. A retrieved chunk counts as a hit
>   when it is in the same file and its line range overlaps an expected range. So ranges must
>   point at the code (or docs section) that actually answers the question; small boundary
>   differences are fine, but a range that misses the relevant code, or a missing file that is
>   essential to answer (especially for multi-hop), is a problem.
> - The reference answer is used by an LLM judge to grade generated answers, so every factual
>   claim in it must be true for this commit. Missing nuance is a minor issue; a false claim is
>   a major one.
> - Negative questions ask about a feature the repository does not have; they have no expected
>   sources and the reference answer says it is not found. Verify by searching the source,
>   documentation and test directories (case-insensitive, with several spellings and synonyms)
>   that the feature really does not exist. If something close exists that would make the
>   question ambiguous for a judge, flag it.
> - Also flag questions that are ambiguous, have several equally valid answers not covered by
>   the sources, or whose type label is clearly wrong.
>
> For EACH item, open the expected ranges and the code they depend on, then decide:
> - OK: sources and reference answer are correct (minor wording issues allowed).
> - MINOR: correct overall, but something should be tightened (a slightly off range, missing
>   nuance, an imprecise phrase). Say exactly what.
> - MAJOR: a false claim in the answer, a wrong or missing essential source, a negative
>   question whose feature exists, or an ambiguous question. Say exactly what is wrong and give
>   the fix (corrected range or corrected sentence), citing file:line evidence.
>
> Work through all items; do not sample. Report one line per item (`<id> OK`, or
> `<id> MINOR: <summary>` / `<id> MAJOR: <summary>`), then a Details section with the evidence
> and proposed fix for each MINOR and MAJOR item, and end with the counts. Keep the report
> factual; do not invent content you did not see in the files.

## `h3.jsonl`

- **Repository:** [`h3js/h3`](https://github.com/h3js/h3), release `v2.0.8`, commit
  `f0e17808ac2439b6434bb10609a961067f4d555b` (MIT license).
- **Items:** 50: 14 locate, 12 explain, 8 multi-hop, 8 docs, 8 negative.

### How it was created

1. Claude (Opus 5.5, in Claude Code) read the source files under `src/` and the documentation
   pages under `docs/` at the pinned commit, and drafted each question and reference answer from
   the code it had read.
2. The line ranges of code sources come from RepoLens's own AST chunker, run over the files with
   splitting and merging turned off, so each range is one function, method or class including
   its leading doc comment. Documentation ranges are heading sections.
3. For each negative question, the topic was searched for in `src/` and `docs/` to confirm that
   h3 has no such feature.
4. The draft went through the review process above, in two rounds. Neither round found a major
   problem. Round 1 raised 7 minor points and round 2 raised 10, all of them missing nuance or
   missing source ranges. The reviewers never contradicted each other. Claude checked every
   point in the code; all held and were corrected.
5. The owner did not review the items one by one. They chose this review process and approved
   the result.

# RepoLens

Code-aware RAG that answers questions about a codebase with exact file and line citations, and proves its retrieval quality with an eval suite in CI.

> **Status:** in early development; nothing is runnable yet. Results and a Quickstart will appear here once they exist.

## What it will do

- Answer natural-language questions about a repository (TypeScript, JavaScript, Python, Markdown) with cited `path:start-end` sources.
- Chunk code by AST (function and class level) as well as by fixed-size windows, and publish a measured comparison.
- Offer a React UI with streaming answers, and an MCP server for clients such as Claude Code.
- Gate pull requests on eval scores in CI.

The design is in [`docs/plans/RepoLens_Plan.md`](docs/plans/RepoLens_Plan.md).

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md) to set it up and [`AGENTS.md`](AGENTS.md) for the commands and rules. The plan lives in [`TASKS.md`](TASKS.md).

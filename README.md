# RepoLens

Code-aware RAG that answers questions about a codebase with exact file and line citations, and proves its retrieval quality with an eval suite in CI.

> **Status:** in early development. You can index a public GitHub repository and ask questions with cited answers; the AST chunker, the eval suite and its results come next.

## Quickstart

You need Docker with Compose v2, a [Voyage AI](https://www.voyageai.com/) API key for embeddings and an [Anthropic](https://www.anthropic.com/api) API key for answers (OpenAI and Azure OpenAI work too; see `.env.example`).

```bash
cp .env.example .env
# In .env, set POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB,
# REPOLENS_VOYAGE_API_KEY and REPOLENS_ANTHROPIC_API_KEY.
docker compose up -d --build --wait
```

Open <http://localhost:8080>, paste a public `https://github.com/` repository URL, wait until it shows **Indexed**, then ask a question. Each `[S1]` chip in the answer shows the cited `path:start-end` on hover or focus.

The API listens on <http://localhost:8000> (`/api/health`, interactive docs at `/docs`). All ports bind to `127.0.0.1` only. `docker compose down` stops everything and keeps the index; `docker compose down --volumes` deletes it too.

Indexing calls the Voyage API and every question calls the LLM provider, both billed to your own keys. RepoLens never runs code from the repositories it indexes.

## What it will do

- Answer natural-language questions about a repository (TypeScript, JavaScript, Python, Markdown) with cited `path:start-end` sources.
- Chunk code by AST (function and class level) as well as by fixed-size windows, and publish a measured comparison.
- Offer a React UI with streaming answers, and an MCP server for clients such as Claude Code.
- Gate pull requests on eval scores in CI.

The design is in [`docs/plans/RepoLens_Plan.md`](docs/plans/RepoLens_Plan.md).

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md) to set it up and [`AGENTS.md`](AGENTS.md) for the commands and rules. The plan lives in [`TASKS.md`](TASKS.md).

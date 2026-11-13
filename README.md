# RepoLens

Code-aware RAG that answers questions about a codebase with exact file and line citations, and proves its retrieval quality with an eval suite in CI.

> **Status:** in development. Indexing, AST chunking, hybrid retrieval and cited, streamed answers work today; the eval suite and its published results come next.

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

## What works today

- **Indexing:** a public GitHub repository (TypeScript, JavaScript, Python, Markdown) is cloned without running its code and chunked three ways, stored side by side so they can be compared:
  - **naive:** windows of about 400 tokens with 15% overlap;
  - **AST:** one chunk per function, method and class, parsed with tree-sitter and headed by its path, symbol and the imports it uses, with small chunks merged and large ones split on statements;
  - **Markdown:** documentation split on its `##` and `###` headings.
- **Retrieval:** vector search with pgvector over `voyage-code-4` embeddings, or hybrid search that fuses it with PostgreSQL full-text search through Reciprocal Rank Fusion (`POST /api/search`, `mode`). Full-text search splits identifiers, so `derive_key` finds `self.derive_key()`.
- **Answers:** streamed over server-sent events; every `[S1]`-style citation is checked against the retrieved sources, and markers that match none are dropped. The chat currently retrieves with vector search.
- **UI:** a repository panel with indexing progress and chunk counts per strategy, a streaming chat with a model selector, and citation chips that show `path:start-end`.

## What comes next

- An optional reranker, golden question sets for two public repositories, retrieval and answer metrics, and a published comparison of the chunking strategies.
- An eval gate in CI, so pull requests cannot lower retrieval quality unnoticed.
- An MCP server for clients such as Claude Code, and an agentic mode.
- A source viewer, a debug drawer, a side-by-side compare mode, and a static read-only demo.

The design is in [`docs/plans/RepoLens_Plan.md`](docs/plans/RepoLens_Plan.md).

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md) to set it up and [`AGENTS.md`](AGENTS.md) for the commands and rules. The plan lives in [`TASKS.md`](TASKS.md).

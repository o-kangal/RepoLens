# SPEC.md - RepoLens Product Specification

<!-- Created from dev-kit 0.1.0. What the product does and why; AGENTS.md says how the
     code must be written, TASKS.md says in which order it gets built. When the two disagree on
     behaviour, this file wins. -->

## 1. Purpose
Code-aware RAG that answers questions about a codebase with exact file and line citations, and proves its retrieval quality with an eval suite in CI.

- **For:** developers who want to ask natural-language questions about a repository, from a web UI or from an MCP client such as Claude Code.
- **Problem:** fixed-size text windows split code at arbitrary points, and claims that a RAG system is better are rarely measured.
- **Success:** grounded, cited answers, plus a published eval report that compares naive and AST chunking with everything else held constant. The full Definition of Done is in `docs/plans/RepoLens_Plan.md` §1.

## 2. Scope
- **In scope:** indexing public GitHub repositories (TypeScript, JavaScript, Python, Markdown); naive, AST and Markdown chunking; hybrid retrieval with an optional reranker; cited streaming answers; an agentic mode for multi-hop questions; an MCP server; a React UI; an eval suite with a CI gate; a static, read-only public demo built from recorded runs.
- **Out of scope (for now):** multi-tenant SaaS, auth and billing; fine-tuning models; other languages; private repositories.

## 3. Users & Key Flows

### 3.1 Index a repository
- **Trigger:** a user submits a repository URL and ref in the UI or through `POST /api/repos`.
- **Steps:** validate the URL, shallow-clone, walk the files, chunk with each strategy, embed, store.
- **Result:** the repository is searchable; the UI shows progress and chunk counts per strategy.
- **Edge cases:** a rejected URL, a repository over the size or file-count limits, and an indexing failure, which ends in a failed status and removes the temporary clone.

### 3.2 Ask a question
- **Trigger:** a user asks a question about an indexed repository.
- **Steps:** retrieve (vector, full-text, RRF, optional rerank), build context within a token budget, stream the answer, validate citations.
- **Result:** a streamed answer whose `[Sx]` markers link to `path:start-end`; clicking one opens the source viewer on the cited lines.
- **Edge cases:** no answer in the indexed code leads to a "not found in the indexed code" answer; invalid citation markers are dropped or flagged; a provider failure produces an error event.

### 3.3 Use from an MCP client
- **Trigger:** an MCP client such as Claude Code calls a RepoLens tool.
- **Tools:** `repolens_search(repo, query, k=8)`, `repolens_ask(repo, question)`, `repolens_list_repos()`.
- **Result:** chunks with `path:start-end` and a snippet, a cited answer, or the list of repositories.

### 3.4 Run the evals
- **Trigger:** a developer runs the experiment runner, or a pull request touches `backend/` or `evals/`.
- **Steps:** run the selected experiments against golden datasets at pinned commits and write a report.
- **Result:** a results table; in CI, a failed check when recall@5 or faithfulness falls below `evals/thresholds.yaml`.

## 4. Business Rules
1. Ingestion accepts only `https://github.com/...` URLs, uses a shallow clone in a temporary directory, enforces size and file-count limits, skips binaries and files over 1 MB, and never executes repository code.
2. Naive chunks use a window of about 400 tokens with 15% overlap, split on line boundaries.
3. AST chunks are one per function, method or class, each with a context header (path, symbol, imports). Nodes over about 800 tokens are split on child statements, nodes under about 40 tokens are merged with siblings, and top-level code forms one module chunk per file.
4. Markdown chunks split on `##` and `###` and keep the heading path as a header.
5. All strategies for a repository are stored side by side, identified by `(url, commit_sha)` and `strategy`. A chunk whose `content_hash` is already stored is not re-embedded.
6. Hybrid retrieval takes the top 30 from vector search and the top 30 from full-text search, fuses them with RRF (`score = Σ 1/(60 + rank)`) and keeps the top 20. The optional reranker keeps the top 8.
7. The context builder fills a token budget, removes overlapping ranges and orders sources by file.
8. Answers come only from the provided sources, and every claim cites `[Sx]`. When the sources hold no answer, the response says "not found in the indexed code".
9. The agent loop runs at most 4 times, and its tools (`search_code`, `read_file_range`, `list_symbols`) are read-only.
10. Hit definition for retrieval metrics: an expected range overlaps a retrieved chunk range.
11. LLM judges use a fixed model and temperature 0, with the rubric in the prompt, and their agreement with about 20 hand-labelled items is reported.

## 5. Non-Functional Requirements
- **Performance:** `docker compose up` gives a working app on a sample repository in under 5 minutes. Latency p50/p95 and cost per query are measured and reported.
- **Accessibility:** WCAG 2.2 AA; keyboard navigable; the chat log uses `role="log"` and `aria-live="polite"`.
- **Security & Privacy:** secrets only through the environment; read-only file access inside the repository root; repository content treated as data. The public demo is static: it has no backend, holds no API keys and makes no provider calls, so it has no running cost. The app itself runs locally with the user's own keys.
- **Supported platforms / browsers:** see Open Questions.

## 6. Open Questions
- The decisions listed under Open Decisions in `TASKS.md`.
- Which browsers the UI officially supports.

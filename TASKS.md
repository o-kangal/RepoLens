# TASKS.md - RepoLens Implementation Plan

<!-- Created from dev-kit 0.1.0. The `next-task` skill executes these tasks in order,
     one per run. Each task is sized to be done and verified in one sitting. Status lines are the
     only completion marker: `⏳ Pending` or `✅ Completed (YYYY-MM-DD)`. -->

## Plan Architecture & Prerequisites
- **Goal:** Build RepoLens as described in `docs/plans/RepoLens_Plan.md`: cited answers about a repository, a measured comparison of naive and AST chunking, an MCP server, a React UI and a CI eval gate.
- **Execution Strategy:** Phase 1 (working cited answer, Tasks 1–16) -> Phase 2 (AST chunking and evals, Tasks 17–26) -> Phase 3 (MCP, agent, polish, Tasks 27–38) -> optional analyzer manifest support (Task 39).
- **Prerequisites:** Node.js and pnpm, `uv`, Docker with Compose, and API keys for the chosen embedding and LLM providers in a local `.env`.
- **Plan references:** `§n` means a section of `docs/plans/RepoLens_Plan.md`.
- **Human steps:** a task whose Human Step is not `None` stays open until the project owner confirms it.

---

## Open Decisions (resolve before the task named in each item)
1. **Embedding model and vector size (before Task 4):** ✅ Resolved (2026-11-05): Voyage `voyage-code-3` with `VECTOR(1024)`, replacing the 1536 in the §5 schema. Revised (2026-11-12, Task 7): `voyage-code-4`, since Voyage now lists `voyage-code-3` under older models; it keeps the 1024 default dimension, so the schema is unchanged. The model is a setting, and vectors from different models are never mixed in one index. The §15 E6 stretch goal can compare `text-embedding-3-small` in the same column through its `dimensions=1024` option, once stored vectors record which model produced them.
2. **Reranker (before Task 20):** Cohere Rerank or a local cross-encoder such as `bge-reranker`?
3. **Eval target repositories (before Task 22):** which public TypeScript repository and which public Python repository, at which pinned commit SHAs?
4. **Tracing (before Task 30):** self-hosted Langfuse or OpenTelemetry?
5. **Demo hosting (before Task 38):** ✅ Resolved (2026-11-12): a static, read-only demo at `repolens.okangal.com`, served by Cloudflare Pages, where `okangal.com` already runs. It has no backend and no API keys, so it has no hosting or usage cost; it replays runs recorded locally against a pinned public repository, chosen at Task 35. The app runs locally with the user's own keys, and the owner pays only for one-off local runs (evals, demo data).
6. **`tokcost` manifest support (before Task 39):** build it once the analyzer exists, or drop it from v1?
7. **Token counter (before Task 6):** ✅ Resolved (2026-11-12): a dependency-free heuristic in `ingest/tokens.py`, scaled toward tiktoken counts, so chunking and unit tests need no tokenizer download. Once the Task 25 runner exists, a stretch experiment can compare naive chunking under this counter and tiktoken `o200k_base`, with the counter as the only changed variable.

---

## Ordered Task Breakdown

### Task 1: Root Workspace and Frontend Scaffold
- **Status:** ✅ Completed (2026-11-05)
- **Type:** Setup
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `SPEC.md`, `docs/plans/RepoLens_Plan.md` (§2, §4, §11)
- **Target Files (Create/Modify):**
  - `package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `tsconfig.json`, `eslint.config.js`
  - `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`
  - `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/tests/App.test.tsx`
- **Step-by-Step Instructions:**
  1. Create a root pnpm workspace containing `frontend`, with `packageManager` set and root scripts `dev`, `typecheck`, `lint`, `test`, `build` that delegate to the frontend.
  2. Scaffold React 19, strict TypeScript and Vite in `frontend/`, adding TanStack Query, Tailwind, Vitest and Testing Library at exact versions.
  3. Render a minimal app shell and test that it renders.
  4. Keep the kit's root `tsconfig.json` and `eslint.config.js`, which already point at `frontend/` through `TS_DIR`, without weakening their rules.
- **Verification:**
```bash
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run lint
pnpm run test
pnpm run build
pnpm audit
```

---

### Task 2: Backend Scaffold and Python CI
- **Status:** ✅ Completed (2026-11-05)
- **Type:** Setup
- **Human Step:** If the CI job uses a new third-party action, the owner lists it under `project.actions` in `.dev-kit.json` and applies the allowed-actions command.
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§2, §4, §12)
- **Target Files (Create/Modify):**
  - `backend/pyproject.toml`, `backend/uv.lock`, `backend/.python-version`
  - `backend/repolens/__init__.py`, `backend/repolens/config.py`, `backend/repolens/main.py`, `backend/repolens/api/health.py`
  - `backend/tests/api/test_health.py`, `.env.example`
  - `.github/workflows/ci.yml`, `.github/dependabot.yml`
- **Step-by-Step Instructions:**
  1. Create a `uv` project for Python 3.12+ with FastAPI and Pydantic v2 at exact versions, plus pytest, ruff, mypy and pip-audit as dev tools.
  2. Load settings only from the environment with `pydantic-settings`, and commit a `.env.example` holding names without values.
  3. Add `GET /api/health` and a test for it.
  4. Add a `python` CI job (ruff, mypy, pytest, pip-audit) with actions pinned to full commit SHAs, and a Dependabot entry for `backend/`.
  5. Name any new third-party action in the report, with the exact `project.actions` change for `.dev-kit.json` and the command from `docs/github-settings.md`.
- **Verification:**
```bash
uv --directory backend sync --locked
uv --directory backend run ruff check .
uv --directory backend run ruff format --check .
uv --directory backend run mypy repolens
uv --directory backend run pytest
uv --directory backend run pip-audit
```

---

### Task 3: Compose Database Service
- **Status:** ✅ Completed (2026-11-05)
- **Type:** Setup
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§2, §12)
- **Target Files (Create/Modify):**
  - `docker-compose.yml`, `.env.example`
- **Step-by-Step Instructions:**
  1. Add a Postgres + pgvector service with a pinned image, a named volume and a health check.
  2. Read credentials from the environment; document their names in `.env.example`.
- **Verification:**
```bash
docker compose config --quiet
docker compose up -d --wait db
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS vector;"'
```

---

### Task 4: Data Model and Migrations
- **Status:** ✅ Completed (2026-11-05)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§5)
- **Target Files (Create/Modify):**
  - `backend/repolens/db/models.py`, `backend/repolens/db/session.py`
  - `backend/alembic.ini`, `backend/migrations/**`
  - `backend/tests/db/test_migrations.py`, `backend/pyproject.toml`, `backend/uv.lock`
- **Step-by-Step Instructions:**
  1. Model `repos` and `chunks` with SQLAlchemy exactly as in §5, using the vector size from Open Decision 1.
  2. Write the Alembic migration, including the extension, the generated `tsv` column, and the HNSW, GIN and `(repo_id, strategy)` indexes.
  3. Test upgrade and downgrade against the Compose database, the `strategy` check constraint, and cascade delete from `repos` to `chunks`.
- **Verification:**
```bash
docker compose up -d --wait db
uv --directory backend run alembic upgrade head
uv --directory backend run alembic downgrade base
uv --directory backend run pytest tests/db
```

---

### Task 5: File Walker
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§3, §12)
- **Target Files (Create/Modify):**
  - `backend/repolens/ingest/walker.py`
  - `backend/tests/ingest/test_walker.py`, `backend/tests/fixtures/**`
- **Step-by-Step Instructions:**
  1. Walk a repository respecting `.gitignore`, and detect TypeScript, JavaScript, Python and Markdown.
  2. Skip binaries and files over 1 MB, and enforce a file-count limit.
  3. Never follow a symlink out of the repository root.
  4. Test each rule with fixtures.
- **Verification:**
```bash
uv --directory backend run pytest tests/ingest/test_walker.py
```

---

### Task 6: Naive and Markdown Chunkers
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§5, §6.1, §6.3)
- **Target Files (Create/Modify):**
  - `backend/repolens/ingest/chunk.py`, `backend/repolens/ingest/naive.py`, `backend/repolens/ingest/markdown.py`, `backend/repolens/ingest/tokens.py`
  - `backend/tests/ingest/test_naive.py`, `backend/tests/ingest/test_markdown.py`
- **Step-by-Step Instructions:**
  1. Define a shared chunk type carrying the fields of the `chunks` table, including `content_hash` and accurate 1-indexed line ranges.
  2. Naive: a window of about 400 tokens with 15% overlap, split on line boundaries.
  3. Markdown: split on `##` and `###`, keeping the heading path as a header ("Install > Docker").
  4. Test line ranges, overlap, empty files, and files with no headings.
- **Verification:**
```bash
uv --directory backend run pytest tests/ingest/test_naive.py tests/ingest/test_markdown.py
```

---

### Task 7: Embedder Interface and First Adapter
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§2, §5)
- **Target Files (Create/Modify):**
  - `backend/repolens/providers/embeddings.py`
  - `backend/tests/providers/test_embeddings.py`, `backend/pyproject.toml`, `backend/uv.lock`, `.env.example`
- **Step-by-Step Instructions:**
  1. Define an embedder interface that exposes the model's dimension and embeds in batches.
  2. Implement the adapter chosen in Open Decision 1, plus a deterministic fake for tests.
  3. Test batching, a dimension mismatch, and provider errors, without network access.
- **Verification:**
```bash
uv --directory backend run pytest tests/providers/test_embeddings.py
```

---

### Task 8: Safe Clone and Ingestion Pipeline
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `SPEC.md`, `docs/plans/RepoLens_Plan.md` (§3, §5, §12)
- **Target Files (Create/Modify):**
  - `backend/repolens/ingest/clone.py`, `backend/repolens/ingest/pipeline.py`
  - `backend/tests/ingest/test_clone.py`, `backend/tests/ingest/test_pipeline.py`
- **Step-by-Step Instructions:**
  1. Accept only `https://github.com/...` URLs, and shallow-clone into a temporary directory with size limits. Never execute repository code, including hooks.
  2. Pipeline: walk, chunk with each strategy, embed, then store under `(url, commit_sha)`.
  3. Skip re-embedding chunks whose `content_hash` is already stored, and always clean up the temporary directory.
  4. Test rejected URLs, the size limit, hash skipping and cleanup on failure.
- **Verification:**
```bash
docker compose up -d --wait db
uv --directory backend run pytest tests/ingest/test_clone.py tests/ingest/test_pipeline.py
```

---

### Task 9: Repository API
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§7 API)
- **Target Files (Create/Modify):**
  - `backend/repolens/api/repos.py`, `backend/repolens/main.py`
  - `backend/tests/api/test_repos.py`
- **Step-by-Step Instructions:**
  1. `POST /api/repos` takes `{url, ref}`, starts indexing as a background job and returns `repo_id`.
  2. `GET /api/repos/{id}` returns the index status and chunk counts per strategy.
  3. Test validation errors, unknown IDs, and the status moving from running to done and to failed.
- **Verification:**
```bash
docker compose up -d --wait db
uv --directory backend run pytest tests/api/test_repos.py
```

---

### Task 10: Vector Search
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§7)
- **Target Files (Create/Modify):**
  - `backend/repolens/retrieval/vector.py`, `backend/repolens/retrieval/types.py`, `backend/repolens/api/search.py`, `backend/repolens/main.py`
  - `backend/tests/retrieval/test_vector.py`, `backend/tests/api/test_search.py`
- **Step-by-Step Instructions:**
  1. Embed the query with the index's model and return the top k by cosine distance, filtered by repository and strategy.
  2. Expose `POST /api/search` for retrieval only.
  3. Test the ranking with known vectors and that results never cross repositories or strategies.
- **Verification:**
```bash
docker compose up -d --wait db
uv --directory backend run pytest tests/retrieval/test_vector.py tests/api/test_search.py
```

---

### Task 11: LLM Provider Adapters
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§2)
- **Target Files (Create/Modify):**
  - `backend/repolens/providers/llm.py`
  - `backend/tests/providers/test_llm.py`, `backend/pyproject.toml`, `backend/uv.lock`, `.env.example`
- **Step-by-Step Instructions:**
  1. Define a streaming LLM interface that also returns token usage.
  2. Implement the Anthropic adapter (primary), the OpenAI/Azure OpenAI adapter, and a scripted fake for tests.
  3. Read the model name from configuration.
  4. Test streaming, usage reporting and provider errors without network access.
- **Verification:**
```bash
uv --directory backend run pytest tests/providers/test_llm.py
```

---

### Task 12: Context Builder, Prompt and Citation Parser
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `SPEC.md`, `docs/plans/RepoLens_Plan.md` (§7, §12)
- **Target Files (Create/Modify):**
  - `backend/repolens/generation/context.py`, `backend/repolens/generation/prompt.py`, `backend/repolens/generation/citations.py`
  - `backend/tests/generation/test_context.py`, `backend/tests/generation/test_prompt.py`, `backend/tests/generation/test_citations.py`
- **Step-by-Step Instructions:**
  1. Context builder: fill a token budget, dedupe overlapping ranges, order by file.
  2. Prompt: answer only from sources, say "not found in the indexed code" otherwise, cite with `[S1]`, `[S2]`, and treat source contents as data, not instructions.
  3. Citation parser: map each `[Sx]` to `path:start-end`, drop or flag invalid markers, and return `{answer, citations[]}`.
  4. Test the budget limit, overlap handling, invalid citations, and a source containing an injection attempt staying wrapped as data.
- **Verification:**
```bash
uv --directory backend run pytest tests/generation
```

---

### Task 13: Streaming Query Endpoint
- **Status:** ✅ Completed (2026-11-12)
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `SPEC.md`, `docs/plans/RepoLens_Plan.md` (§7)
- **Target Files (Create/Modify):**
  - `backend/repolens/api/query.py`, `backend/repolens/generation/pipeline.py`, `backend/repolens/main.py`, `backend/repolens/config.py`, `.env.example`
  - `backend/tests/api/test_query.py`
- **Step-by-Step Instructions:**
  1. `POST /api/query` takes `{repo_id, question, strategy, rerank, model}` and streams over SSE: retrieve, build context, generate.
  2. `model` is optional and must be one of the models allowed in configuration; `GET /api/models` lists them and the default.
  3. Finish the stream with the validated citations, and send a typed error event on failure.
  4. Test the event order, error events and model validation with the fake embedder and fake LLM.
- **Verification:**
```bash
docker compose up -d --wait db
uv --directory backend run pytest tests/api/test_query.py
```

---

### Task 14: Streaming Chat UI
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§11)
- **Target Files (Create/Modify):**
  - `frontend/src/api/**`, `frontend/src/features/chat/**`, `frontend/src/App.tsx`
  - `frontend/tests/chat/**`
- **Step-by-Step Instructions:**
  1. Write a typed API client that parses the SSE stream, with no `any`.
  2. Build the chat with a streaming answer, using `role="log"` and `aria-live="polite"`, and with loading, empty and error states.
  3. Add a model selector filled from `GET /api/models`.
  4. Test streaming, error handling, the model selector and the ARIA roles with a mocked stream.
- **Verification:**
```bash
pnpm run typecheck
pnpm run lint
pnpm run test
```

---

### Task 15: Citation Chips and Repo Panel
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§11)
- **Target Files (Create/Modify):**
  - `frontend/src/features/chat/**`, `frontend/src/features/repos/**`, `frontend/src/App.tsx`
  - `frontend/tests/chat/**`, `frontend/tests/repos/**`
- **Step-by-Step Instructions:**
  1. Render `[S1]` markers as chips, with hover and keyboard focus showing `path:lines`.
  2. Build the repo panel: add a repository URL, poll indexing progress with TanStack Query, and show chunk stats per strategy.
  3. Test chip rendering, an invalid citation marker, and polling stopping when indexing ends.
- **Verification:**
```bash
pnpm run typecheck
pnpm run lint
pnpm run test
```

---

### Task 16: Full-Stack Compose and Phase 1 Exit
- **Status:** ⏳ Pending
- **Type:** Setup
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§1, §13)
- **Target Files (Create/Modify):**
  - `backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`
- **Step-by-Step Instructions:**
  1. Add backend and frontend services with pinned base images, running as non-root, with migrations applied on start.
  2. Write the Quickstart section of the README.
  3. Index a small public sample repository through the UI and ask a question. **Exit:** the answer streams and its citations point at real lines.
- **Verification:**
```bash
docker compose config --quiet
docker compose up -d --build --wait
curl --fail --silent http://localhost:8000/api/health
```

---

### Task 17: AST Chunker Core
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§6.2)
- **Target Files (Create/Modify):**
  - `backend/repolens/ingest/ast_chunker.py`
  - `backend/tests/ingest/test_ast_chunker.py`, `backend/tests/fixtures/**`, `backend/pyproject.toml`, `backend/uv.lock`
- **Step-by-Step Instructions:**
  1. Parse TypeScript, JavaScript and Python with tree-sitter, emitting one chunk per function, method and class, with `symbol` and `kind` set.
  2. Prepend the context header (path, symbol, imports) to each chunk.
  3. Test arrow functions, class methods, TSX components, Python decorators, and invalid code that must not crash the run.
- **Verification:**
```bash
uv --directory backend run pytest tests/ingest/test_ast_chunker.py
```

---

### Task 18: AST Size Rules and Pipeline Integration
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§5, §6.2)
- **Target Files (Create/Modify):**
  - `backend/repolens/ingest/ast_chunker.py`, `backend/repolens/ingest/pipeline.py`
  - `backend/tests/ingest/test_ast_chunker.py`, `backend/tests/ingest/test_pipeline.py`
- **Step-by-Step Instructions:**
  1. Split nodes over about 800 tokens on child statements, keeping the header in each part.
  2. Merge nodes under about 40 tokens with their siblings.
  3. Emit one "module" chunk per file for top-level code.
  4. Store AST chunks beside naive chunks for the same repository.
  5. Test each rule, plus line ranges staying accurate after a split or a merge.
- **Verification:**
```bash
docker compose up -d --wait db
uv --directory backend run pytest tests/ingest/test_ast_chunker.py tests/ingest/test_pipeline.py
```

---

### Task 19: Full-Text Search and RRF Fusion
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§7)
- **Target Files (Create/Modify):**
  - `backend/repolens/retrieval/fulltext.py`, `backend/repolens/retrieval/fusion.py`, `backend/repolens/retrieval/search.py`, `backend/repolens/api/search.py`
  - `backend/tests/retrieval/test_fulltext.py`, `backend/tests/retrieval/test_fusion.py`
- **Step-by-Step Instructions:**
  1. Add full-text search using `ts_rank`, top k = 30.
  2. Fuse with vector search through RRF (`score = Σ 1/(60 + rank)`), keeping the top 20.
  3. Make the search mode selectable (vector or hybrid), since the experiments compare both.
  4. Test exact identifier matches and RRF scores against hand-computed values.
- **Verification:**
```bash
docker compose up -d --wait db
uv --directory backend run pytest tests/retrieval
```

---

### Task 20: Optional Reranker
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§2, §7)
- **Target Files (Create/Modify):**
  - `backend/repolens/retrieval/rerank.py`, `backend/repolens/retrieval/search.py`
  - `backend/tests/retrieval/test_rerank.py`, `backend/pyproject.toml`, `backend/uv.lock`, `.env.example`
- **Step-by-Step Instructions:**
  1. Define a reranker interface and implement the adapter chosen in Open Decision 2.
  2. Apply it behind the `rerank` flag, keeping the top 8.
  3. Test the flag, the ordering with a fake reranker, and a defined behaviour when the reranker fails.
- **Verification:**
```bash
uv --directory backend run pytest tests/retrieval/test_rerank.py
```

---

### Task 21: Retrieval Metrics and Dataset Schema
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§9.2, §9.3)
- **Target Files (Create/Modify):**
  - `evals/metrics.py`, `evals/dataset.py`, `evals/tests/test_metrics.py`, `evals/tests/test_dataset.py`
- **Step-by-Step Instructions:**
  1. Validate golden JSONL items with Pydantic, including the five question types: locate, explain, multi-hop, docs, negative.
  2. Implement recall@k (k=5,10), MRR and nDCG, where a hit means the expected range overlaps a retrieved chunk range.
  3. Test the metrics against hand-computed values, including negative questions that have no expected sources.
- **Verification:**
```bash
uv --directory backend run pytest ../evals/tests/test_metrics.py ../evals/tests/test_dataset.py
```

---

### Task 22: Golden Dataset, TypeScript Target
- **Status:** ⏳ Pending
- **Type:** Data
- **Human Step:** The owner verifies every dataset item.
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§9.1, §9.2)
- **Target Files (Create/Modify):**
  - `evals/datasets/<typescript-repo>.jsonl`, `evals/datasets/README.md`
- **Step-by-Step Instructions:**
  1. Pin the repository and commit SHA from Open Decision 3.
  2. Draft about 50 questions from the real code, covering all five question types.
  3. **Human step:** the project owner verifies every item. Record in `evals/datasets/README.md` how the dataset was created, and only what really happened.
  4. Mark the task complete only after the owner confirms verification.
- **Verification:**
```bash
uv --directory backend run python ../evals/dataset.py ../evals/datasets/<typescript-repo>.jsonl
```

---

### Task 23: Golden Dataset, Python Target
- **Status:** ⏳ Pending
- **Type:** Data
- **Human Step:** The owner verifies every dataset item.
- **Read-Only Context Files:** `AGENTS.md`, `evals/datasets/README.md`, `docs/plans/RepoLens_Plan.md` (§9.1, §9.2)
- **Target Files (Create/Modify):**
  - `evals/datasets/<python-repo>.jsonl`, `evals/datasets/README.md`
- **Step-by-Step Instructions:**
  1. Pin the repository and commit SHA from Open Decision 3.
  2. Draft about 50 questions from the real code, covering all five question types.
  3. **Human step:** the project owner verifies every item.
  4. Mark the task complete only after the owner confirms verification.
- **Verification:**
```bash
uv --directory backend run python ../evals/dataset.py ../evals/datasets/<python-repo>.jsonl
```

---

### Task 24: Generation Metrics and Judge Calibration
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** The owner hand-labels about 20 calibration items.
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§9.3)
- **Target Files (Create/Modify):**
  - `evals/judge.py`, `evals/metrics.py`, `evals/calibration/labels.jsonl`, `evals/calibration/README.md`
  - `evals/tests/test_judge.py`
- **Step-by-Step Instructions:**
  1. Implement faithfulness (0–1), answer correctness (1–5 rubric) and refusal accuracy, using a fixed judge model, temperature 0, and the rubric in the prompt.
  2. **Human step:** the project owner hand-labels about 20 items.
  3. Run the judge on those items and report its agreement with the owner's labels, whatever the result is.
  4. Test judge output parsing and malformed responses with a fake LLM.
- **Verification:**
```bash
uv --directory backend run pytest ../evals/tests/test_judge.py
```

---

### Task 25: Experiment Runner and Report
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§9.3, §9.4)
- **Target Files (Create/Modify):**
  - `evals/run_evals.py`, `evals/experiments.py`, `evals/reports/<date>.md`, `evals/tests/test_run_evals.py`
- **Step-by-Step Instructions:**
  1. Implement experiments E1–E4 from §9.4, changing one variable at a time against the same index.
  2. Record all retrieval and generation metrics, plus context tokens, latency p50/p95 and cost per query.
  3. Write a Markdown and JSON report. **Exit:** a results table comparing E1–E4 from real runs.
  4. Test the runner and the report format with fakes. Never hand-edit numbers in a report.
- **Verification:**
```bash
uv --directory backend run pytest ../evals/tests/test_run_evals.py
uv --directory backend run python ../evals/run_evals.py --repo <python-repo> --exp E1,E2,E3,E4
```

---

### Task 26: CI Eval Gate
- **Status:** ⏳ Pending
- **Type:** CI
- **Human Step:** The owner adds the API key secrets to GitHub.
- **Read-Only Context Files:** `AGENTS.md`, `evals/reports/`, `docs/plans/RepoLens_Plan.md` (§9.5)
- **Target Files (Create/Modify):**
  - `.github/workflows/evals.yml`, `evals/thresholds.yaml`, `evals/datasets/smoke.jsonl`, `evals/run_evals.py`
- **Step-by-Step Instructions:**
  1. Run E4 on a 20-question smoke subset for pull requests touching `backend/` or `evals/`.
  2. Fail when recall@5 or faithfulness drops below `thresholds.yaml`, with thresholds derived from the Task 25 results.
  3. Cache embeddings for the pinned commit, read API keys from GitHub secrets, skip forks, use least-privilege `permissions`, and pin actions to full commit SHAs.
  4. List the secrets the owner must add in the report; do not set them.
- **Verification:**
```bash
docker run --rm -v "$PWD:/repo" -w /repo docker.io/rhysd/actionlint:1.7.12 -color=false
uv --directory backend run python ../evals/run_evals.py --repo <python-repo> --exp E4 --smoke --gate
```

---

### Task 27: MCP Server
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§10)
- **Target Files (Create/Modify):**
  - `backend/repolens/mcp_server.py`, `backend/tests/test_mcp_server.py`, `backend/pyproject.toml`, `backend/uv.lock`, `README.md`
- **Step-by-Step Instructions:**
  1. Expose `repolens_search(repo, query, k=8)`, `repolens_ask(repo, question)` and `repolens_list_repos()` with the official MCP Python SDK.
  2. Add a "Use with Claude Code" README section with the exact configuration snippet, tested against a real Claude Code session.
  3. Test the tools' inputs and outputs in-process with fakes.
- **Verification:**
```bash
uv --directory backend run pytest tests/test_mcp_server.py
```

---

### Task 28: Agent Tools and LangGraph Graph
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§8, §12)
- **Target Files (Create/Modify):**
  - `backend/repolens/agent/tools.py`, `backend/repolens/agent/graph.py`
  - `backend/tests/agent/test_tools.py`, `backend/tests/agent/test_graph.py`, `backend/pyproject.toml`, `backend/uv.lock`
- **Step-by-Step Instructions:**
  1. Add read-only tools: `search_code`, `read_file_range(path, start, end)` and `list_symbols(path)`.
  2. Make `read_file_range` resolve paths inside the repository root only.
  3. Build the graph `plan → search → read_file_range → [loop ≤ 4] → answer`.
  4. Test path traversal attempts, including symlinks, and the loop limit with a scripted LLM.
- **Verification:**
```bash
uv --directory backend run pytest tests/agent
```

---

### Task 29: Agentic Experiment (E5)
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§8, §9.4)
- **Target Files (Create/Modify):**
  - `evals/experiments.py`, `evals/run_evals.py`, `evals/reports/<date>.md`, `evals/tests/test_run_evals.py`
- **Step-by-Step Instructions:**
  1. Add E5 to the runner.
  2. Compare single-shot RAG (E4) with the agent (E5) on the multi-hop subset for accuracy, tokens and latency.
  3. Publish the trade-off table from real runs, including the case where the agent does not win.
- **Verification:**
```bash
uv --directory backend run pytest ../evals/tests/test_run_evals.py
uv --directory backend run python ../evals/run_evals.py --repo <python-repo> --exp E4,E5 --type multi-hop
```

---

### Task 30: Tracing and Cost per Request
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§2, §9.3)
- **Target Files (Create/Modify):**
  - `backend/repolens/observability.py`, `backend/repolens/generation/pipeline.py`
  - `backend/tests/test_observability.py`, `docker-compose.yml`, `.env.example`, `backend/pyproject.toml`, `backend/uv.lock`
- **Step-by-Step Instructions:**
  1. Integrate the tool from Open Decision 4, adding its service to Compose if it is self-hosted.
  2. Trace retrieval and generation, and record token usage and cost per request, reading prices from configuration rather than code.
  3. Make the app work with tracing disabled, and test that path.
- **Verification:**
```bash
docker compose config --quiet
uv --directory backend run pytest tests/test_observability.py
```

---

### Task 31: Source Viewer
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§11, §12)
- **Target Files (Create/Modify):**
  - `frontend/src/features/sources/**`, `frontend/tests/sources/**`
  - `backend/repolens/api/sources.py`, `backend/repolens/main.py`, `backend/tests/api/test_sources.py`
- **Step-by-Step Instructions:**
  1. Add a read-only backend route for source ranges, restricted to the indexed repository.
  2. Open the viewer when a citation chip is clicked, with syntax highlighting and the cited lines highlighted.
  3. Test keyboard use and focus handling, and test path traversal attempts on the route.
- **Verification:**
```bash
pnpm run typecheck
pnpm run lint
pnpm run test
uv --directory backend run pytest tests/api/test_sources.py
```

---

### Task 32: Debug Drawer
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§11)
- **Target Files (Create/Modify):**
  - `frontend/src/features/debug/**`, `frontend/tests/debug/**`, `backend/repolens/api/query.py`, `backend/tests/api/test_query.py`
- **Step-by-Step Instructions:**
  1. Send retrieved chunks with scores, token count and request cost in the query stream.
  2. Show them in a drawer with a strategy toggle.
  3. Test that the drawer renders real payloads and handles missing cost data without inventing a number.
- **Verification:**
```bash
pnpm run typecheck
pnpm run lint
pnpm run test
uv --directory backend run pytest tests/api/test_query.py
```

---

### Task 33: Compare Mode
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§11)
- **Target Files (Create/Modify):**
  - `frontend/src/features/compare/**`, `frontend/tests/compare/**`
- **Step-by-Step Instructions:**
  1. Send the same question with the naive and AST strategies and stream both answers side by side.
  2. Handle one side failing without losing the other.
  3. Test independent streaming and partial failure.
- **Verification:**
```bash
pnpm run typecheck
pnpm run lint
pnpm run test
```

---

### Task 34: UI Quality Pass and Playwright E2E
- **Status:** ⏳ Pending
- **Type:** Test
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§11)
- **Target Files (Create/Modify):**
  - `frontend/src/**`, `frontend/tests/e2e/**`, `frontend/playwright.config.ts`, `frontend/package.json`, `pnpm-lock.yaml`, `.github/workflows/ci.yml`
- **Step-by-Step Instructions:**
  1. Add the dark and light themes, and check keyboard navigation and the error, empty and loading states of every feature.
  2. Add one Playwright E2E: add a repository, ask a question, click a citation. Run it against fake providers so it needs no API keys.
  3. Add the E2E to CI, with actions pinned to full commit SHAs.
- **Verification:**
```bash
pnpm run typecheck
pnpm run lint
pnpm run test
pnpm --filter frontend exec playwright test
```

---

### Task 35: Static Demo Export
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** The owner picks the pinned public repository and the demo questions, and runs the export locally with their own keys.
- **Read-Only Context Files:** `AGENTS.md`, `SPEC.md`, `docs/plans/RepoLens_Plan.md` (§11, §12)
- **Target Files (Create/Modify):**
  - `backend/repolens/demo/export.py`, `backend/tests/demo/test_export.py`
  - `frontend/src/api/**`, `frontend/tests/api/**`, `frontend/public/demo/**`
- **Step-by-Step Instructions:**
  1. Add an export command that runs a fixed question list against a locally indexed repository at a pinned commit, for each strategy, and writes the answers, citations, retrieved chunks with scores and usage, plus the cited source files, as static JSON under `frontend/public/demo/`.
  2. Add a static data mode to the frontend API client, selected at build time, that serves those files through the same interface; it disables indexing and new questions and says so in the UI.
  3. Test the export with the fakes, and the static client against a fixture export.
  4. **Human step:** the owner chooses the repository and questions and runs the export.
- **Verification:**
```bash
uv --directory backend run pytest tests/demo/test_export.py
pnpm run typecheck
pnpm run test
pnpm run build
```

---

### Task 36: Architecture Doc and ADRs
- **Status:** ⏳ Pending
- **Type:** Docs
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§3, §4)
- **Target Files (Create/Modify):**
  - `docs/architecture.md`, `docs/decisions/0001-*.md`, `docs/decisions/0002-*.md`, `docs/decisions/0003-*.md`
- **Step-by-Step Instructions:**
  1. Write the architecture doc with the Mermaid diagram, updated to match what was really built.
  2. Write ADRs for pgvector vs a dedicated vector DB, tree-sitter vs the TypeScript compiler API, and RRF vs weighted fusion, citing project results where they exist.
- **Verification:**
```bash
ls docs/architecture.md docs/decisions/000{1,2,3}-*.md
grep -rn "TODO" docs/architecture.md docs/decisions && exit 1 || true
```

---

### Task 37: README
- **Status:** ⏳ Pending
- **Type:** Docs
- **Human Step:** The owner records or approves the demo GIF.
- **Read-Only Context Files:** `AGENTS.md`, `evals/reports/`, `docs/plans/RepoLens_Plan.md` (§14)
- **Target Files (Create/Modify):**
  - `README.md`, `docs/screenshots/**`
- **Step-by-Step Instructions:**
  1. Follow the skeleton in §14: pitch and demo GIF, results table, Quickstart, architecture, how evals work, Claude Code usage, design decisions, limitations.
  2. Copy results only from the latest generated report, and state the limitations honestly.
  3. **Human step:** the project owner records or approves the demo GIF.
- **Verification:**
```bash
docker compose down
docker compose up -d --build --wait
curl --fail --silent http://localhost:8000/api/health
```

---

### Task 38: Deploy Static Demo and Definition of Done
- **Status:** ⏳ Pending
- **Type:** Deploy
- **Human Step:** The owner creates the Cloudflare Pages project and the `repolens.okangal.com` custom domain.
- **Read-Only Context Files:** `AGENTS.md`, `SECURITY.md`, `docs/plans/RepoLens_Plan.md` (§1, §12)
- **Target Files (Create/Modify):**
  - `docs/deploy.md`, `README.md`
- **Step-by-Step Instructions:**
  1. Write the deployment guide for Cloudflare Pages (Open Decision 5): the build command, output directory and environment for the static demo build, and the custom domain. Check that the Cloudflare build image supports the pinned pnpm; if it does not, document a build that does.
  2. **Human step:** the owner creates the Pages project from the GitHub repository and adds `repolens.okangal.com`.
  3. Add the live link to the README and walk through the Definition of Done in §1, reporting any item still open.
- **Verification:**
```bash
curl --fail --silent --head https://repolens.okangal.com/
```

---

### Task 39: Analyzer Manifest Support (Optional)
- **Status:** ⏳ Pending
- **Type:** Feature
- **Human Step:** None
- **Read-Only Context Files:** `AGENTS.md`, `docs/plans/RepoLens_Plan.md` (§6.4)
- **Target Files (Create/Modify):**
  - `backend/repolens/ingest/manifest.py`, `backend/repolens/ingest/pipeline.py`, `backend/tests/ingest/test_manifest.py`
- **Step-by-Step Instructions:**
  1. Accept a `tokcost` chunk manifest (`--manifest file.json`) with `{path, symbol, kind, startLine, endLine, tokens}` for TS/JS repositories.
  2. Validate it strictly, rejecting paths outside the repository root.
  3. Compare its output with the tree-sitter chunks, and report the differences.
- **Verification:**
```bash
uv --directory backend run pytest tests/ingest/test_manifest.py
```

---

## Dependency Graph & Critical Path

```text
Phase 1: cited answer
Task 1 (Workspace + frontend) ──► Task 14 (Chat UI) ──► Task 15 (Chips + repo panel)
Task 2 (Backend + CI) ──► Task 3 (Compose DB) ──► Task 4 (Data model)
Task 2 ──► Task 5 (Walker) ──► Task 6 (Naive + Markdown) ──► Task 7 (Embedder)
Tasks 4, 6, 7 ──► Task 8 (Clone + pipeline) ──► Task 9 (Repo API) ──► Task 10 (Vector search)
Task 2 ──► Task 11 (LLM) ──► Task 12 (Context + citations)
Tasks 10, 12 ──► Task 13 (SSE query) ──► Task 14
Tasks 9, 13, 15 ──► Task 16 (Full compose, Phase 1 exit)

Phase 2: AST + evals
Task 16 ──► Task 17 (AST core) ──► Task 18 (AST rules + pipeline)
Task 16 ──► Task 19 (FTS + RRF) ──► Task 20 (Reranker)
Task 10 ──► Task 21 (Metrics + schema) ──► Tasks 22, 23 (Golden datasets)
Tasks 13, 21 ──► Task 24 (Judge)
Tasks 18, 20, 22, 23, 24 ──► Task 25 (Runner + report) ──► Task 26 (CI gate)

Phase 3: MCP, agent, polish
Tasks 13, 19 ──► Task 27 (MCP)
Tasks 19, 25 ──► Task 28 (Agent) ──► Task 29 (E5)
Task 13 ──► Task 30 (Tracing)
Task 15 ──► Task 31 (Source viewer) ──► Task 34 (Quality + E2E)
Tasks 15, 30 ──► Task 32 (Debug drawer) ──► Task 34
Tasks 15, 18 ──► Task 33 (Compare) ──► Task 34
Tasks 31, 32, 33 ──► Task 35 (Static demo)
Tasks 26, 27, 29, 34, 35 ──► Task 36 (Architecture + ADRs) ──► Task 37 (README) ──► Task 38 (Deploy)

Optional
Tasks 18 and an existing `tokcost` manifest ──► Task 39 (Manifest support)
```

**Critical path:** Tasks 2 → 5 → 6 → 7 → 8 → 9 → 10 → 13 → 16 → 18 → 25 → 26 → 36 → 37 → 38.

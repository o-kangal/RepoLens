# AGENTS.md - Engineering Constraints & Agent Context

<!-- Created from dev-kit 0.1.0. Agents may not edit this file (.claude/settings.json); change it yourself. -->

## System & Architecture Overview
- **Product:** RepoLens — Code-aware RAG that answers questions about a codebase with exact file and line citations, and proves its retrieval quality with an eval suite in CI.
- **Stack:** Python 3.12+, FastAPI, Pydantic v2 and `uv` (backend, evals); PostgreSQL with pgvector and `tsvector`; tree-sitter; React 19, strict TypeScript, Vite, TanStack Query and Tailwind (frontend); pytest, Vitest, Testing Library and Playwright; Docker Compose and GitHub Actions. Hosting is an Open Decision in `TASKS.md`. Exact versions live in the manifests and lockfiles.
- **Plan:** `docs/plans/RepoLens_Plan.md` is the full design; `TASKS.md` is the order of work.
- **Primary Language Invariant:** All source code, interfaces, comments, commit messages and error messages are written in **English (en-US)**.

---

## Directory Layout & Domain Boundaries

```
.
├─ backend/repolens/   # Application code. Routers in api/ hold no retrieval or generation logic.
│  ├─ api/             # FastAPI routers
│  ├─ ingest/          # Walker, chunkers, pipeline. The only code that clones repositories.
│  ├─ retrieval/       # Vector, full-text, fusion, rerank
│  ├─ generation/      # Context builder, prompt, citation parser
│  ├─ providers/       # Embedding and LLM adapters. The only code that calls those providers.
│  ├─ agent/           # LangGraph mode. Tools are read-only.
│  └─ db/              # SQLAlchemy models. Schema changes only through Alembic migrations.
├─ backend/tests/      # pytest. Unit tests use fakes, not the network.
├─ evals/              # Golden datasets, metrics, judge, runner, thresholds, generated reports
├─ frontend/           # React app and its tests. Talks to the backend only through src/api/.
├─ docs/plans/         # Design plan
├─ docs/decisions/     # ADRs
└─ docs/private/       # Git-ignored personal notes. Never commit or link from public files.
```

---

## Deterministic CLI Commands

Always run these commands from the repository root:

- **Install Dependencies:** `pnpm install --frozen-lockfile`
- **Development:** `pnpm dev`
- **Type Check:** `pnpm run typecheck`
- **Linter:** `pnpm run lint`
- **Tests:** `pnpm run test`
- **Production Build:** `pnpm run build`
- **Python Install:** `uv --directory backend sync --locked`
- **Python Lint:** `uv --directory backend run ruff check .` and `uv --directory backend run ruff format --check .`
- **Python Type Check:** `uv --directory backend run mypy repolens`
- **Python Tests:** `uv --directory backend run pytest`
- **Python Dependency Audit:** `uv --directory backend run pip-audit`
- **Database:** `docker compose up -d --wait db`

The commands above start working once Tasks 1–3 have created the tooling.

---

## Architectural Invariants & Data Rules

1. Every returned citation resolves to a retrieved source and its exact `path:start-end`; invalid markers are dropped or flagged, never returned as valid.
2. Ingestion accepts only `https://github.com/` URLs and never executes repository code or hooks. File access stays inside the repository root, and agent tools are read-only.
3. Indexed content always reaches the LLM wrapped as data, never as instructions, and never gains access to a write-capable tool.
4. Published metrics come only from generated reports and are never hand-edited. Compared experiments differ in one variable. Thresholds change only with a stated reason and the owner's approval.

---

## Hard Guardrails (Agent Prohibitions)

- **DO NOT** use TypeScript `any`. Use precise types, or `unknown` with type guards.
- **DO NOT** install dependencies with `pnpm add` (denied in `.claude/settings.json`). Write them into `package.json` by hand at an exact version (`npm view <pkg> version`), then run `pnpm install`.
- **DO NOT** pin a GitHub Action by tag. Pin it to a full commit SHA with the tag in a comment; Dependabot keeps the pins current.
- **DO NOT** add a third-party GitHub Action without telling the owner. GitHub blocks it until the owner lists it under `project.actions` in `.dev-kit.json` and applies `docs/github-settings.md`.
- **DO NOT** commit secrets, tokens or `.env` files. gitleaks runs on every push.
- **DO NOT** invent facts in content, docs or metadata (names, dates, numbers, links). Ask instead.
- **DO NOT** weaken a check to make it pass (skipped tests, `eslint-disable`, lowered CI thresholds) without saying so in the report.
- **DO NOT** install Python packages with `uv add` or `uv pip install` (denied). Write them into `backend/pyproject.toml` by hand at an exact version, then run `uv lock`.
- **DO NOT** commit or reference `docs/private/`. It holds personal notes and stays out of the public repository.
- **DO NOT** call real embedding, LLM or reranker APIs from unit tests. Use the fakes; real calls belong to evals.

---

## Known Pitfalls

<!-- Things that cost time once, so they don't cost it again. The next-task skill reads this list
     before every task. Add an entry whenever a task hits a non-obvious problem. -->

- The pnpm release-age policy rejects versions published in the last 24 hours. Pick an older version; never add `minimumReleaseAgeExclude` entries. If a stale lockfile is rejected, regenerate it.
- Generate `pnpm-lock.yaml` with pnpm 12 itself. A global pnpm 10 that switches to the pinned version omits `packageManagerDependencies`, and CI then fails on `--frozen-lockfile` while local checks pass.
- `backend/` uses `exclude-newer = "1 day"` for the same release-age rule, so pin a direct dependency to a version older than that.

---

## Agent Task Execution Protocol

Tasks come from `TASKS.md`, one at a time (the `next-task` skill runs this loop):

1. **Context Discovery:** Read the task's Read-Only Context Files and the SPEC.md sections it points to.
2. **Plan & Boundary Check:** List the files you'll touch; stay inside the task's Target Files; check the invariants above.
3. **Implementation:** Clean, minimal code. No dead code, no premature abstractions. Tests for the rules the task names.
4. **Verification:** Run the task's Verification commands, then the repo-wide checks above. Zero errors, zero new warnings.
5. **Report & Commit:** Mark the task completed in TASKS.md, report, propose a conventional commit message and wait for approval before committing and pushing.

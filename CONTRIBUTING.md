# Contributing to RepoLens

Thank you for helping! Bug reports, ideas and pull requests are all welcome. For security issues, please follow [`SECURITY.md`](SECURITY.md) instead of opening an issue.

## Getting started

Install Node.js 24+ and pnpm (the version in `packageManager` of `package.json`; `corepack enable` picks it up), then:

```bash
pnpm install --frozen-lockfile
pnpm dev
```

## Before you open a pull request

Run the checks listed under "Deterministic CLI Commands" in [`AGENTS.md`](AGENTS.md). All of them must pass with no new warnings; CI runs them on every pull request, together with gitleaks and CodeQL.

Add tests for what you change: the rules a change touches should be covered by a test that would fail without it.

## How the code is written

[`AGENTS.md`](AGENTS.md) holds the engineering rules every change follows, for people and coding agents alike. [`SPEC.md`](SPEC.md) describes the intended behaviour.

- **English in the source.** Code, comments and commit messages are in English.
- **Dependencies** are added at exact versions.
- **Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, …), with a short summary line.

---
name: next-task
description: Execute the next (or a specified) task from RepoLens's TASKS.md end to end following the AGENTS.md protocol — pick the first task not completed whose dependencies are done, load only its context files and the matching project skills, implement the target files, run the task's verification plus repo-wide checks, mark it complete, report, and propose a commit for approval. Use when the user says "next task", "continue the plan", "do task 5", or otherwise asks to make progress on TASKS.md.
argument-hint: "[task number — optional]"
---

# Execute a TASKS.md Task

TASKS.md is an ordered plan. Each task is sized to be done and verified in one sitting, and later tasks assume earlier ones are finished as written. So the job is to finish **one** task fully and stop. Don't drift into the next task, and don't hand back a half-verified one.

## 1. Pick the task

- If `$ARGUMENTS` names a task number, use it. Otherwise pick the first task in document order whose status is not completed.
- The status line directly under a task heading is the only completion marker. Use exactly these forms so the next run can find them:
  ```
  - **Status:** ⏳ Pending
  - **Status:** ✅ Completed (YYYY-MM-DD)
  ```
- If TASKS.md has **Open Decisions** that the chosen task depends on, stop and ask the user to resolve them first. Record the answers in TASKS.md (and SPEC.md if they change behaviour).
- Check the "Dependency Graph & Critical Path" section. If a prerequisite of the chosen task isn't completed, stop and tell the user which one is missing.

## 2. Load context, deliberately small

- Read the task's **Read-Only Context Files**, plus the parts of SPEC.md its rules point to. AGENTS.md is already in context; reread its **Known Pitfalls**.
- Load the project skills in `.claude/skills/` whose description matches the task's domain. They hold the rules the task text only implies.
- If a file the task needs to read doesn't exist, check whether its producing task is really complete before inventing its contents.

## 3. Boundary check before writing code

Write a short plan: the files you'll touch and the invariants at risk. Confirm the plan stays inside the task's **Target Files**. If the task genuinely needs a file outside that list (a shared type, a test helper), touch it minimally and name it in the report.

When the docs conflict or the plan can't run as written, prefer SPEC.md over TASKS.md for behaviour and AGENTS.md for guardrails, and mention the conflict in the report. Never resolve one by weakening a guardrail, and never route around a deny rule in `.claude/settings.json` (shell redirection, another tool): ask the user instead.

A new third-party GitHub Action needs the owner: `.dev-kit.json` is read-only for agents, so name the action in the report and give the exact `project.actions` change and the command from `docs/github-settings.md`.

Dependency installs are denied. Write a new dependency into the manifest by hand at an exact, current version (`npm view <pkg> version`, `cargo search <crate>`), then install from it.

## 4. Implement

Write clean, minimal code: no dead code, no speculative abstractions. Write tests for the rules the task names. A task whose verification is "run the tests" but which adds no tests hasn't been verified.

## 5. Verify

Run, from the repo root:

1. The task's own **Verification** command(s), exactly as written.
2. The repo-wide checks listed under "Deterministic CLI Commands" in AGENTS.md, once the tooling exists.

If something fails, fix the cause and rerun. The bar is zero errors and zero new warnings. If a failure is outside the task's control (missing system libraries, no network, a permission deny), stop and report it with the output. Don't mark the task done.

If the task hit a non-obvious problem worth remembering, propose a **Known Pitfalls** entry for AGENTS.md in the report (agents can't edit AGENTS.md; the user adds it).

## 6. Mark complete

If the task's **Human Step** is not `None`, tell the user what is needed and wait. Mark the task complete only after the user confirms the step in this conversation, however good the automated checks look.

Change the task's status line to `✅ Completed (<today>)`. Change nothing else in TASKS.md, apart from recording answered Open Decisions.

## 7. Report, then propose the commit

```
## Task <N>: <title> — Completed

**Changed files:** <list, with created/modified>
**Verification:**
- `<command>` → pass/fail (<one-line summary>)
**Deviations & conflicts:** <files outside Target Files, doc conflicts resolved and how, or "none">
**Proposed AGENTS.md pitfall:** <entry, or "none">
**Next:** Task <N+1>: <title>
```

Then propose a conventional commit message (`feat:`, `fix:`, `chore:`, `docs:`, `ci:`, …; summary line under 72 characters, body explaining why) and **wait for the user's approval**. Once approved: check `git status` and `git add -n .` for files that must not be committed (secrets, `.env`, build output), commit, and push. Don't commit before approval, and don't start the next task.

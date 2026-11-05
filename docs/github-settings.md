# GitHub Repository Settings

The settings of `o-kangal/RepoLens` that the code relies on, and how to apply them with the GitHub CLI. Run the commands yourself (they change the repository for everyone), after setting `R=repos/o-kangal/RepoLens`. Move each row to **Applied** with the date once it is done.

## Every repository

| Setting | Value | Command |
|---|---|---|
| Default `GITHUB_TOKEN` permissions | read-only, cannot approve PRs | `gh api -X PUT $R/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false` |
| Allowed actions | GitHub-owned plus the list below | `gh api -X PUT $R/actions/permissions -F enabled=true -f allowed_actions=selected -F sha_pinning_required=false` |
| Selected actions | see the JSON | `gh api -X PUT $R/actions/permissions/selected-actions --input -` with `{"github_owned_allowed":true,"verified_allowed":false,"patterns_allowed":["pnpm/action-setup@*","astral-sh/setup-uv@*"]}` |
| Dependabot alerts and security updates | on | `gh api -X PUT $R/vulnerability-alerts && gh api -X PUT $R/automated-security-fixes` |
| Pull requests merge by squash only, merged branches deleted | | `gh api -X PATCH $R -F allow_merge_commit=false -F allow_rebase_merge=false -F allow_squash_merge=true -F delete_branch_on_merge=true` |

A workflow that needs a new third-party action fails until the action is added to the selected list. Add it to `project.actions` in `.dev-kit.json`, rerender this file's list with `/dev-kit update`, and run the command again. The kit's `check` command reports actions that aren't listed.

Leave **SHA pinning required** off: it also applies to the workflows GitHub runs for Dependabot, which call actions by tag, so with it on Dependabot silently stops (no runs, no rebases). Our own workflows pin every action to a commit SHA anyway, and Dependabot keeps those pins current.

## Public repositories

Rulesets are free only on public repositories (or paid plans).

| Setting | Command |
|---|---|
| Ruleset for `main`: no deletion, no force-push, linear history, signed commits | `gh api -X POST $R/rulesets --input .github/rulesets/main.json` |
| Ruleset for `v*` tags: no deletion, no moving, signed | `gh api -X POST $R/rulesets --input .github/rulesets/release-tags.json` |
| Private vulnerability reporting (`SECURITY.md` points to it) | `gh api -X PUT $R/private-vulnerability-reporting` |
| Secret scanning and push protection | `gh api -X PATCH $R --input -` with `{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}` |
| Workflow runs from first-time and external contributors need approval | `gh api -X PUT $R/actions/permissions/fork-pr-contributor-approval -f approval_policy=all_external_contributors` |

CodeQL (`.github/workflows/codeql.yml`) skips its analysis while the repository is private. Leave CodeQL's "default setup" off, since it conflicts with the workflow.

## Commit signing

Commits and release tags are signed with SSH keys (`git config gpg.format ssh`, `commit.gpgsign true`, `tag.gpgsign true`). The `main` ruleset rejects unsigned commits, so add your public key on GitHub as a **signing key** (Settings → SSH and GPG keys), or with `gh ssh-key add ~/.ssh/id_ed25519.pub --type signing`. Set this up before the first commit.

Every commit also records your email address, which is visible on a public repository. To keep a personal address private, use your GitHub noreply address in this repository: `git config user.email "$(gh api user --jq '"\(.id)+\(.login)@users.noreply.github.com"')"`.

## Applied

| Setting | Date |
|---|---|
| Default `GITHUB_TOKEN` permissions | 2026-11-05 |
| Allowed actions and selected actions | 2026-11-05 |
| Dependabot alerts and security updates | 2026-11-05 |
| Pull request merge settings | 2026-11-05 |
| Rulesets for `main` and `v*` tags | 2026-11-05 |
| Private vulnerability reporting | 2026-11-05 |
| Secret scanning and push protection | 2026-11-05 |
| Workflow approval for first-time and external contributors | 2026-11-05 |

---
name: issue-to-main
description: Run an end-to-end GitHub issue development workflow for a personal repository using an isolated .worktrees worktree. Use when an agent must start from the latest origin/main, implement an existing GitHub issue, validate locally, open a ready PR to main, wait for required CI, diagnose and fix CI failures in the same worktree, enable GitHub auto-merge, confirm the PR is actually merged and the issue is closed, then remove the worktree and branches. Trigger for requests such as "implement issue #123", "take this issue through PR and merge", or "use the worktree PR flow". Requires local git, GitHub CLI gh, and a GitHub repository whose default branch is main.
---

# Issue to Main

Execute one GitHub issue as a transaction:

`open issue -> latest origin/main -> isolated worktree -> implementation -> PR -> CI recovery loop -> auto-merge -> closed issue -> cleanup`

## Completion invariant

Do not report success until all conditions are true:

1. Every required CI check has completed successfully.
2. The pull request state is `MERGED`, not merely open with auto-merge enabled.
3. The target issue state is `CLOSED`.
4. The linked worktree and its local task branch have been removed.

If any condition cannot be reached safely, report `BLOCKED` with the exact current state and evidence. Never claim partial completion as success.

## Required input

Obtain or resolve:

- Local repository path.
- Existing open GitHub issue number or URL.
- A concise implementation slug.
- Repository-specific validation commands.

Use `main` as the base branch. Require `main` to be the repository default branch so `Closes #N` can close the issue on merge.

## Workflow

### 1. Run preflight checks

1. Verify `git` and `gh` are installed.
2. Run `gh auth status` and stop if authentication is unavailable.
3. Inspect repository instructions such as `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, and relevant README files.
4. Verify the issue is open and understand its acceptance criteria:

```bash
gh issue view <issue> --json number,title,body,state,url,labels
```

5. Verify the remote default branch is `main`:

```bash
gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'
```

6. Verify repository settings support the autonomous flow. Read [references/repository-setup.md](references/repository-setup.md) when setup is uncertain.

Never bypass branch protection with `--admin`. Never disable checks, reviews, or rules to make the workflow pass.

### 2. Create the isolated worktree

Run from the primary checkout, not from an existing linked worktree:

```bash
python <skill-path>/scripts/start_worktree.py \
  --repo <primary-checkout> \
  --issue <issue-number> \
  --slug <short-slug>
```

The helper must fetch `origin/main` and create:

- Branch: `issue/<number>-<slug>`
- Worktree: `.worktrees/issue-<number>-<slug>`

Perform all implementation, validation, commits, and pushes inside that worktree. Do not edit directly on `main`.

### 3. Implement and validate

1. Work only on the issue scope.
2. Inspect existing patterns before changing code.
3. Add or update tests when behavior changes.
4. Run the repository's relevant tests, lint, formatting, type checks, and build commands.
5. Inspect `git status -sb` and `git diff` before staging.
6. Stage only intended files and create a focused commit.
7. Push the task branch:

```bash
git push -u origin "$(git branch --show-current)"
```

Do not hide failures by weakening tests, skipping checks, or changing CI requirements unless the issue explicitly requires that change.

### 4. Create a ready pull request

Create a non-draft PR targeting `main`. Include a closing keyword in the body:

```markdown
## Summary
- <what changed>

## Validation
- `<command>`

Closes #<issue-number>
```

Create the PR with explicit base and head branches:

```bash
gh pr create \
  --base main \
  --head "$(git branch --show-current)" \
  --title "<concise title>" \
  --body-file <pr-body-file>
```

Capture the PR number or URL. Confirm the PR is not a draft and its closing issue reference resolves to the intended issue.

### 5. Wait, recover CI, and merge

Run the watcher from the task worktree:

```bash
python <skill-path>/scripts/watch_pr.py \
  --pr <pr-number-or-url> \
  --issue <issue-number> \
  --merge-method squash \
  --close-issue-fallback
```

Interpret the watcher exit code:

- `0`: PR merged and issue closed. Continue to cleanup.
- `20`: Required CI failed or was cancelled. Follow the CI recovery loop below, then run the watcher again.
- `21`: A non-CI blocker exists, such as merge conflicts, required human review, draft state, missing auto-merge configuration, or invalid repository setup. Resolve it safely and rerun, or report `BLOCKED`.
- `22`: The PR merged but the issue could not be confirmed closed. Repair the issue link or close the explicitly authorized issue, then verify again.
- Any other nonzero code: inspect the emitted error and report a precise blocker if it cannot be repaired.

The watcher waits without a timeout by default. It enables auto-merge against the current head commit, waits for required checks, detects merge blockers, waits through a merge queue when present, verifies the PR reaches `MERGED`, and verifies the issue reaches `CLOSED`.

### 6. Recover from CI failure

Read [references/ci-recovery.md](references/ci-recovery.md) and use this loop:

1. Read the failed check names and URLs emitted by `watch_pr.py`.
2. For GitHub Actions, inspect the failed run:

```bash
gh run view <run-id> --log-failed
```

3. Classify the failure:
   - Code, test, lint, type, or build failure caused by the PR: fix it in the same worktree.
   - Clearly transient infrastructure or network failure: rerun failed jobs with `gh run rerun <run-id> --failed`.
   - Missing secret, unavailable external service, permission failure, required human review, or unrelated repository failure: report `BLOCKED` unless a safe repository-local fix exists.
4. Run relevant local validation again.
5. Commit and push the fix to the same branch.
6. Run `watch_pr.py` again. It must re-enable auto-merge for the new head commit when needed.

Continue while each iteration has an evidence-based next fix. Stop rather than loop blindly when the same unexplained failure repeats or required credentials and external systems are unavailable.

### 7. Clean up only after merge and issue closure

Run from the primary checkout or pass its path explicitly:

```bash
python <skill-path>/scripts/cleanup_worktree.py \
  --repo <primary-checkout> \
  --pr <pr-number-or-url> \
  --issue <issue-number> \
  --delete-remote
```

The cleanup helper must refuse destructive cleanup unless the PR is merged and the issue is closed. It must also refuse to remove a dirty worktree.

### 8. Report the result

Return a concise final record:

```text
SUCCESS
Issue: #<number> CLOSED
PR: #<number> MERGED into main
CI: all required checks passed
Merge commit: <sha or URL>
Worktree: removed
Local branch: removed
Remote branch: removed or already absent
```

For an unresolved state, return:

```text
BLOCKED
Issue: <state>
PR: <state and URL>
CI: <failed, pending, or passed checks>
Blocker: <specific cause>
Worktree: <preserved path>
Next safe action: <one concrete action>
```

Preserve the worktree on failure so work and diagnostics remain available.

## Bundled resources

- `scripts/start_worktree.py`: Fetch `origin/main`, create the issue branch and `.worktrees` worktree, and emit structured metadata.
- `scripts/watch_pr.py`: Validate the PR/issue relationship, require registered required checks, enable auto-merge, detect CI or merge blockers, wait for merge, and confirm issue closure.
- `scripts/cleanup_worktree.py`: Verify terminal GitHub state, remove a clean worktree, delete the task branch, optionally delete the remote branch, fetch `main`, and prune worktree metadata.
- `references/repository-setup.md`: Repository prerequisites and merge-queue notes.
- `references/ci-recovery.md`: CI diagnosis and retry rules.

# Repository Setup

Use this checklist before relying on a fully autonomous issue-to-main flow.

## Required repository state

- The repository is hosted on GitHub.
- The default branch is named `main`.
- GitHub CLI is authenticated with permission to read issues, push branches, create PRs, enable auto-merge, and close issues.
- Auto-merge is enabled in repository settings.
- Squash merge is enabled when `--merge-method squash` is used.
- At least one CI check is required by the `main` branch protection rule or ruleset.
- The required checks run for `pull_request` events.
- Required human approval is disabled for a fully unattended personal workflow. A PR author generally cannot satisfy a required independent review alone.
- The issue exists and is open before development begins.

## Why required checks matter

GitHub auto-merge only waits for merge requirements. If CI workflows are optional, GitHub may merge before those workflows finish. Configure the CI jobs that define correctness as required checks on `main`.

The watcher refuses to start auto-merge when it cannot observe registered required checks. This prevents a repository with no effective CI gate from merging immediately.

## Closing the issue

Put `Closes #N` in the PR body and target the repository default branch. The normal path is automatic issue closure when the PR merges.

The watcher can use `--close-issue-fallback` to close the explicitly supplied issue after the PR is confirmed merged and the normal closure grace period expires. It records whether fallback closure was used.

## Merge queues

The workflow can wait for a GitHub merge queue. When a merge queue is required, configure required GitHub Actions workflows for both PR and merge-group events:

```yaml
on:
  pull_request:
  merge_group:
```

Without the `merge_group` event, required checks may never report for the queued merge group.

Do not bypass a merge queue with administrator privileges.

## Local repository layout

Keep linked worktrees under:

```text
<primary-checkout>/.worktrees/
```

The start helper adds `.worktrees/` to the repository-local Git exclude file. This does not modify the committed `.gitignore`.

Use one issue, one branch, one worktree, and one PR. Do not reuse the same branch for unrelated issues.

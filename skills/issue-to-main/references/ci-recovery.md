# CI Recovery

## Triage order

1. Read the check name, workflow, state, and URL emitted by `watch_pr.py`.
2. Confirm the failing run belongs to the current PR head SHA.
3. Inspect failed GitHub Actions steps with:

```bash
gh run view <run-id> --log-failed
```

4. Compare the failure with the local diff and reproduce locally when possible.
5. Make the smallest evidence-based fix.
6. Run the relevant local checks before pushing.
7. Commit and push to the same PR branch.
8. Run `watch_pr.py` again.

## Failure classes

### PR-caused code failure

Examples include test failures, lint errors, type errors, build errors, migrations, generated files, snapshots, and formatting.

Fix the underlying defect. Do not delete meaningful assertions, broaden ignores, or mark tests flaky merely to produce a green check.

### Transient infrastructure failure

Examples include runner startup failures, package registry outages, network timeouts, or clearly unrelated service interruptions.

When evidence supports a transient classification, rerun only failed jobs:

```bash
gh run rerun <run-id> --failed
```

Do not rerun repeatedly without new evidence. A repeated failure should be treated as deterministic until proven otherwise.

### Repository or permission blocker

Examples include unavailable secrets, expired credentials, billing or quota failures, inaccessible protected environments, required human approval, branch-rule misconfiguration, or an unrelated failing required check.

Do not modify application code speculatively. Preserve the worktree and report `BLOCKED` with the exact check and log evidence.

### Merge conflict

Fetch `origin/main`, resolve the conflict in the task worktree, run all relevant validation, commit, and push. Never force-push `main`. Prefer a normal merge or rebase only when repository policy permits it.

## Retry discipline

Continue only when the next action is justified by fresh evidence. Stop and report a blocker when:

- The same failure recurs after an attempted fix and no new root cause is visible.
- A required secret, permission, or external service is unavailable.
- A required human review cannot be satisfied autonomously.
- The failure is unrelated to the PR and cannot be repaired safely in scope.
- Repair would require bypassing repository protections.

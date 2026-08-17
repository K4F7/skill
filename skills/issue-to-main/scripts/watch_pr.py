#!/usr/bin/env python3
"""Wait for required PR checks, enable auto-merge, and verify issue closure."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Sequence

EXIT_CI_FAILED = 20
EXIT_BLOCKED = 21
EXIT_ISSUE_OPEN = 22


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class GhError(RuntimeError):
    def __init__(self, args: Sequence[str], result: CommandResult):
        self.args_list = list(args)
        self.result = result
        super().__init__(
            f"gh command failed ({result.returncode}): gh {' '.join(args)}\n{result.stderr.strip()}"
        )


def run_gh(args: Sequence[str], *, repo: str | None = None, check: bool = True) -> CommandResult:
    command = ["gh", *args]
    if repo:
        command.extend(["-R", repo])
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = CommandResult(proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise GhError(args, result)
    return result


def gh_json(args: Sequence[str], *, repo: str | None = None) -> Any:
    result = run_gh(args, repo=repo)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from gh {' '.join(args)}: {result.stdout!r}") from exc


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def pr_view(pr: str, repo: str) -> dict[str, Any]:
    fields = (
        "number,url,title,body,state,isDraft,baseRefName,headRefName,headRefOid,"
        "mergeStateStatus,mergeable,reviewDecision,autoMergeRequest,mergedAt,"
        "mergeCommit,closingIssuesReferences"
    )
    return gh_json(["pr", "view", pr, "--json", fields], repo=repo)


def issue_view(issue: int, repo: str) -> dict[str, Any]:
    return gh_json(
        ["issue", "view", str(issue), "--json", "number,title,state,closedAt,url"],
        repo=repo,
    )


def get_checks(pr: str, repo: str, *, required: bool) -> list[dict[str, Any]]:
    args = [
        "pr",
        "checks",
        pr,
        "--json",
        "bucket,name,state,link,workflow,startedAt,completedAt",
    ]
    if required:
        args.append("--required")
    result = run_gh(args, repo=repo, check=False)
    if result.stdout.strip():
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"could not parse check JSON: {result.stdout!r}") from exc
        if isinstance(value, list):
            return value
    lower = (result.stderr or "").lower()
    if "no checks" in lower or "no required checks" in lower:
        return []
    if result.returncode in (0, 1, 8) and not result.stdout.strip():
        return []
    raise GhError(args, result)


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    summary: dict[str, list[dict[str, Any]]] = {
        "pass": [],
        "pending": [],
        "fail": [],
        "skipping": [],
        "cancel": [],
        "other": [],
    }
    for check in checks:
        bucket = str(check.get("bucket") or "other").lower()
        if bucket not in summary:
            bucket = "other"
        summary[bucket].append(check)
    return summary


def closing_reference_present(pr_data: dict[str, Any], issue: int) -> bool:
    refs = pr_data.get("closingIssuesReferences") or []
    for ref in refs:
        try:
            if int(ref.get("number")) == issue:
                return True
        except (TypeError, ValueError, AttributeError):
            continue
    body = str(pr_data.get("body") or "")
    keyword = r"(?:close(?:s|d)?|fix(?:es|ed)?|resolve(?:s|d)?)"
    return re.search(rf"(?i)\b{keyword}\s*:?\s*#\s*{issue}\b", body) is not None


def enable_auto_merge(pr_data: dict[str, Any], pr: str, repo: str, method: str) -> tuple[bool, str]:
    if str(pr_data.get("state")).upper() == "MERGED":
        return True, "already merged"
    if pr_data.get("isDraft"):
        return False, "pull request is a draft"
    head_sha = str(pr_data.get("headRefOid") or "")
    args = ["pr", "merge", pr, "--auto", f"--{method}"]
    if head_sha:
        args.extend(["--match-head-commit", head_sha])
    first = run_gh(args, repo=repo, check=False)
    if first.returncode == 0:
        return True, first.stdout.strip()

    # Merge queues choose their own merge method. Retry without a strategy.
    fallback_args = ["pr", "merge", pr, "--auto"]
    if head_sha:
        fallback_args.extend(["--match-head-commit", head_sha])
    second = run_gh(fallback_args, repo=repo, check=False)
    if second.returncode == 0:
        return True, second.stdout.strip()

    refreshed = pr_view(pr, repo)
    if str(refreshed.get("state")).upper() == "MERGED":
        return True, "merged while enabling auto-merge"
    message = second.stderr.strip() or first.stderr.strip() or "could not enable auto-merge"
    return False, message


def wait_for_issue_closed(
    issue: int,
    repo: str,
    pr_data: dict[str, Any],
    *,
    grace_seconds: int,
    close_fallback: bool,
    interval: int,
) -> tuple[bool, bool, dict[str, Any]]:
    deadline = time.monotonic() + max(0, grace_seconds)
    last = issue_view(issue, repo)
    while str(last.get("state")).upper() != "CLOSED" and time.monotonic() < deadline:
        time.sleep(max(1, interval))
        last = issue_view(issue, repo)
    if str(last.get("state")).upper() == "CLOSED":
        return True, False, last

    if not close_fallback:
        return False, False, last

    pr_url = str(pr_data.get("url") or f"PR #{pr_data.get('number')}")
    comment = f"Closing after confirmed merge of {pr_url}."
    result = run_gh(
        ["issue", "close", str(issue), "--comment", comment],
        repo=repo,
        check=False,
    )
    if result.returncode != 0:
        return False, True, last
    confirmed = issue_view(issue, repo)
    return str(confirmed.get("state")).upper() == "CLOSED", True, confirmed


def complete_after_merge(
    args: argparse.Namespace,
    repo: str,
    pr_data: dict[str, Any],
    required_checks: list[dict[str, Any]],
) -> int:
    closed, fallback_used, issue_data = wait_for_issue_closed(
        args.issue,
        repo,
        pr_data,
        grace_seconds=args.issue_close_grace,
        close_fallback=args.close_issue_fallback,
        interval=args.interval,
    )
    if not closed:
        emit(
            {
                "status": "issue-open-after-merge",
                "exit_code": EXIT_ISSUE_OPEN,
                "repo": repo,
                "pr": pr_data,
                "issue": issue_data,
                "required_checks": required_checks,
                "fallback_attempted": fallback_used,
            }
        )
        return EXIT_ISSUE_OPEN

    merge_commit = pr_data.get("mergeCommit") or {}
    emit(
        {
            "status": "merged-and-closed",
            "repo": repo,
            "pr_number": pr_data.get("number"),
            "pr_url": pr_data.get("url"),
            "pr_state": pr_data.get("state"),
            "merged_at": pr_data.get("mergedAt"),
            "merge_commit": merge_commit,
            "issue_number": issue_data.get("number"),
            "issue_url": issue_data.get("url"),
            "issue_state": issue_data.get("state"),
            "issue_close_fallback_used": fallback_used,
            "required_checks": required_checks,
        }
    )
    return 0


def blocked(reason: str, repo: str, pr_data: dict[str, Any], checks: list[dict[str, Any]]) -> int:
    emit(
        {
            "status": "blocked",
            "exit_code": EXIT_BLOCKED,
            "reason": reason,
            "repo": repo,
            "pr": pr_data,
            "required_checks": checks,
        }
    )
    return EXIT_BLOCKED


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, help="PR number, URL, or branch")
    parser.add_argument("--issue", required=True, type=int, help="Issue that the PR must close")
    parser.add_argument("--repo", help="OWNER/REPO; defaults to the current repository")
    parser.add_argument("--merge-method", choices=("squash", "merge", "rebase"), default="squash")
    parser.add_argument("--interval", type=int, default=10, help="Polling interval in seconds")
    parser.add_argument(
        "--initial-checks-grace",
        type=int,
        default=90,
        help="Seconds to wait for required CI checks to register",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Overall timeout; 0 waits indefinitely",
    )
    parser.add_argument(
        "--issue-close-grace",
        type=int,
        default=60,
        help="Seconds to wait for linked issue auto-closure",
    )
    parser.add_argument(
        "--close-issue-fallback",
        action="store_true",
        help="Close the supplied issue after confirmed merge if keyword closure does not occur",
    )
    args = parser.parse_args()

    if shutil.which("gh") is None:
        print("ERROR: required command not found: gh", file=sys.stderr)
        return 1
    if args.interval < 1 or args.initial_checks_grace < 0 or args.timeout_seconds < 0:
        print("ERROR: interval and timeout values must be non-negative; interval must be at least 1", file=sys.stderr)
        return 1

    try:
        auth = run_gh(["auth", "status"], check=False)
        if auth.returncode != 0:
            raise RuntimeError(f"GitHub CLI is not authenticated: {auth.stderr.strip()}")

        repo_view_args = ["repo", "view"]
        if args.repo:
            repo_view_args.append(args.repo)
        repo_view_args.extend(["--json", "nameWithOwner,defaultBranchRef"])
        repo_meta = gh_json(repo_view_args)
        repo = str(repo_meta.get("nameWithOwner") or args.repo or "")
        if not repo:
            raise RuntimeError("could not resolve repository name")
        default_branch = str((repo_meta.get("defaultBranchRef") or {}).get("name") or "")
        if default_branch != "main":
            return blocked(
                f"repository default branch must be main, found {default_branch or 'unknown'}",
                repo,
                pr_view(args.pr, repo),
                [],
            )

        pr_data = pr_view(args.pr, repo)
        if str(pr_data.get("baseRefName")) != "main":
            return blocked("pull request base branch is not main", repo, pr_data, [])
        if pr_data.get("isDraft"):
            return blocked("pull request is a draft", repo, pr_data, [])
        if not closing_reference_present(pr_data, args.issue):
            return blocked(
                f"pull request body does not contain a closing reference for issue #{args.issue}",
                repo,
                pr_data,
                [],
            )

        started = time.monotonic()
        check_deadline = started + args.initial_checks_grace
        required_checks: list[dict[str, Any]] = []
        all_checks: list[dict[str, Any]] = []

        while not required_checks:
            pr_data = pr_view(args.pr, repo)
            if str(pr_data.get("state")).upper() == "MERGED":
                return complete_after_merge(args, repo, pr_data, required_checks)
            if str(pr_data.get("state")).upper() == "CLOSED":
                return blocked("pull request was closed without merging", repo, pr_data, required_checks)
            all_checks = get_checks(args.pr, repo, required=False)
            required_checks = get_checks(args.pr, repo, required=True)
            if required_checks:
                break
            if time.monotonic() >= check_deadline:
                reason = (
                    "CI checks exist but none are required by main; configure required checks before auto-merge"
                    if all_checks
                    else "no CI checks registered for the pull request before the grace period expired"
                )
                return blocked(reason, repo, pr_data, all_checks)
            log("Waiting for required CI checks to register...")
            time.sleep(args.interval)

        enabled, message = enable_auto_merge(pr_data, args.pr, repo, args.merge_method)
        if not enabled:
            return blocked(f"could not enable auto-merge: {message}", repo, pr_data, required_checks)
        log("Auto-merge enabled; waiting for required CI and merge completion.")
        enabled_head = str(pr_data.get("headRefOid") or "")
        last_signature: tuple[Any, ...] | None = None

        while True:
            if args.timeout_seconds and time.monotonic() - started >= args.timeout_seconds:
                return blocked("watch timeout expired", repo, pr_data, required_checks)

            pr_data = pr_view(args.pr, repo)
            state = str(pr_data.get("state") or "").upper()
            if state == "MERGED":
                return complete_after_merge(args, repo, pr_data, required_checks)
            if state == "CLOSED":
                return blocked("pull request was closed without merging", repo, pr_data, required_checks)
            if pr_data.get("isDraft"):
                return blocked("pull request became a draft", repo, pr_data, required_checks)

            mergeable = str(pr_data.get("mergeable") or "").upper()
            merge_state = str(pr_data.get("mergeStateStatus") or "").upper()
            review = str(pr_data.get("reviewDecision") or "").upper()
            if mergeable == "CONFLICTING" or merge_state == "DIRTY":
                return blocked("pull request has merge conflicts", repo, pr_data, required_checks)
            if review == "CHANGES_REQUESTED":
                return blocked("pull request has requested changes", repo, pr_data, required_checks)

            required_checks = get_checks(args.pr, repo, required=True)
            if not required_checks:
                return blocked("required CI checks disappeared from the pull request", repo, pr_data, [])
            summary = summarize_checks(required_checks)
            failed = summary["fail"] + summary["cancel"]
            pending = summary["pending"] + summary["other"]

            if failed:
                emit(
                    {
                        "status": "ci-failed",
                        "exit_code": EXIT_CI_FAILED,
                        "repo": repo,
                        "pr": pr_data,
                        "failed_checks": failed,
                        "required_checks": required_checks,
                    }
                )
                return EXIT_CI_FAILED

            current_head = str(pr_data.get("headRefOid") or "")
            if current_head and current_head != enabled_head:
                enabled, message = enable_auto_merge(pr_data, args.pr, repo, args.merge_method)
                if not enabled:
                    return blocked(
                        f"could not re-enable auto-merge for updated head {current_head}: {message}",
                        repo,
                        pr_data,
                        required_checks,
                    )
                enabled_head = current_head
                log(f"Auto-merge re-enabled for updated head {current_head[:12]}.")

            if not pending and review == "REVIEW_REQUIRED":
                return blocked(
                    "required CI passed but an independent review is still required",
                    repo,
                    pr_data,
                    required_checks,
                )

            signature = (
                state,
                merge_state,
                review,
                tuple(sorted((str(c.get("name")), str(c.get("bucket"))) for c in required_checks)),
            )
            if signature != last_signature:
                passed_count = len(summary["pass"]) + len(summary["skipping"])
                log(
                    f"PR #{pr_data.get('number')}: required checks "
                    f"{passed_count}/{len(required_checks)} complete; "
                    f"mergeState={merge_state or 'UNKNOWN'} review={review or 'NONE'}"
                )
                last_signature = signature

            time.sleep(args.interval)

    except (RuntimeError, GhError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted while waiting; PR state was not reported as successful.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

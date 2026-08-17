#!/usr/bin/env python3
"""Remove an issue worktree and branches after the PR is merged and issue closed."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


class CommandError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, stdout: str, stderr: str):
        self.command = list(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"command failed ({returncode}): {' '.join(command)}\n{stderr.strip()}")


def run(command: Sequence[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise CommandError(command, proc.returncode, proc.stdout, proc.stderr)
    return proc


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], check=check)


def gh_json(args: Sequence[str], repo: Path) -> Any:
    proc = run(["gh", *args], cwd=repo)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from gh {' '.join(args)}: {proc.stdout!r}") from exc


def resolve_repo(path: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    top = git(candidate, "rev-parse", "--show-toplevel").stdout.strip()
    repo = Path(top).resolve()
    entries = parse_worktrees(repo)
    if not entries:
        raise RuntimeError("could not resolve primary worktree")
    primary = Path(entries[0]["path"]).resolve()
    if repo != primary:
        raise RuntimeError(f"--repo must point to the primary checkout: {primary}")
    return repo


def parse_worktrees(repo: Path) -> list[dict[str, str]]:
    output = git(repo, "worktree", "list", "--porcelain").stdout
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines() + [""]:
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "branch":
            current["branch_ref"] = value
            current["branch"] = value.removeprefix("refs/heads/")
        else:
            current[key] = value
    return entries


def branch_exists(repo: Path, branch: str) -> bool:
    return git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def remote_branch_exists(repo: Path, remote: str, branch: str) -> bool:
    return git(repo, "ls-remote", "--exit-code", "--heads", remote, branch, check=False).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Primary checkout path")
    parser.add_argument("--pr", required=True, help="Merged PR number, URL, or branch")
    parser.add_argument("--issue", required=True, type=int, help="Closed issue number")
    parser.add_argument("--worktree", help="Expected worktree path; otherwise resolved by PR branch")
    parser.add_argument("--branch", help="Expected task branch; otherwise resolved from PR")
    parser.add_argument("--remote", default="origin", help="Git remote (default: origin)")
    parser.add_argument("--base", default="main", help="Base branch to fetch after cleanup")
    parser.add_argument("--delete-remote", action="store_true", help="Delete the remote task branch")
    args = parser.parse_args()

    try:
        if shutil.which("git") is None or shutil.which("gh") is None:
            raise RuntimeError("git and gh are required")
        repo = resolve_repo(args.repo)

        pr_data = gh_json(
            [
                "pr",
                "view",
                args.pr,
                "--json",
                "number,url,state,mergedAt,headRefName,baseRefName,mergeCommit",
            ],
            repo,
        )
        if str(pr_data.get("state")).upper() != "MERGED":
            raise RuntimeError(f"refusing cleanup: PR is not merged (state={pr_data.get('state')})")
        if str(pr_data.get("baseRefName")) != args.base:
            raise RuntimeError(
                f"refusing cleanup: PR base is {pr_data.get('baseRefName')}, expected {args.base}"
            )

        issue_data = gh_json(
            ["issue", "view", str(args.issue), "--json", "number,url,state,closedAt"],
            repo,
        )
        if str(issue_data.get("state")).upper() != "CLOSED":
            raise RuntimeError(
                f"refusing cleanup: issue #{args.issue} is not closed (state={issue_data.get('state')})"
            )

        pr_branch = str(pr_data.get("headRefName") or "")
        branch = args.branch or pr_branch
        if not branch:
            raise RuntimeError("could not resolve PR head branch")
        if args.branch and args.branch != pr_branch:
            raise RuntimeError(f"branch mismatch: supplied {args.branch}, PR head is {pr_branch}")

        entries = parse_worktrees(repo)
        matching = [entry for entry in entries if entry.get("branch") == branch]
        worktree_path: Path | None = None
        if args.worktree:
            supplied = Path(args.worktree).expanduser().resolve()
            if matching and supplied != Path(matching[0]["path"]).resolve():
                raise RuntimeError(
                    f"worktree mismatch: supplied {supplied}, branch {branch} is at {matching[0]['path']}"
                )
            worktree_path = supplied
        elif matching:
            worktree_path = Path(matching[0]["path"]).resolve()

        removed_worktree = False
        if worktree_path and worktree_path.exists():
            if worktree_path == repo:
                raise RuntimeError("refusing to remove the primary checkout")
            dirty = git(worktree_path, "status", "--porcelain").stdout.strip()
            if dirty:
                raise RuntimeError(f"refusing to remove dirty worktree: {worktree_path}\n{dirty}")
            git(repo, "worktree", "remove", str(worktree_path))
            removed_worktree = True
        elif matching:
            # Registered but missing paths are pruned below.
            worktree_path = Path(matching[0]["path"]).resolve()

        local_deleted = False
        if branch_exists(repo, branch):
            git(repo, "branch", "-D", branch)
            local_deleted = True

        remote_deleted = False
        if args.delete_remote and remote_branch_exists(repo, args.remote, branch):
            git(repo, "push", args.remote, "--delete", branch)
            remote_deleted = True

        git(repo, "fetch", args.remote, args.base)
        git(repo, "worktree", "prune")

        result = {
            "status": "cleaned",
            "repo": str(repo),
            "pr_number": pr_data.get("number"),
            "pr_url": pr_data.get("url"),
            "issue_number": issue_data.get("number"),
            "issue_url": issue_data.get("url"),
            "branch": branch,
            "worktree": str(worktree_path) if worktree_path else None,
            "worktree_removed": removed_worktree,
            "local_branch_deleted": local_deleted,
            "remote_branch_deleted": remote_deleted,
            "remote_branch_delete_requested": args.delete_remote,
            "base_ref_fetched": f"{args.remote}/{args.base}",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (RuntimeError, CommandError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

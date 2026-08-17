#!/usr/bin/env python3
"""Create an issue-scoped Git worktree from the latest origin/main."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


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


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required command not found: {name}")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], check=check)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:56].rstrip("-") or "task"


def resolve_repo(path: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    proc = git(candidate, "rev-parse", "--show-toplevel")
    return Path(proc.stdout.strip()).resolve()


def issue_title(repo: Path, issue: int) -> str:
    require_command("gh")
    proc = run(
        ["gh", "issue", "view", str(issue), "--json", "title,state", "--jq", ".state + \"\\t\" + .title"],
        cwd=repo,
    )
    state, sep, title = proc.stdout.strip().partition("\t")
    if not sep:
        raise RuntimeError("could not parse issue title from gh output")
    if state.upper() != "OPEN":
        raise RuntimeError(f"issue #{issue} is not open (state={state})")
    return title


def add_local_exclude(repo: Path, entry: str) -> None:
    common_dir_raw = git(repo, "rev-parse", "--git-common-dir").stdout.strip()
    common_dir = Path(common_dir_raw)
    if not common_dir.is_absolute():
        common_dir = (repo / common_dir).resolve()
    exclude = common_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    if entry not in lines:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{entry}\n")


def ref_exists(repo: Path, ref: str) -> bool:
    return git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def remote_branch_exists(repo: Path, remote: str, branch: str) -> bool:
    proc = git(repo, "ls-remote", "--exit-code", "--heads", remote, branch, check=False)
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Primary checkout path (default: current directory)")
    parser.add_argument("--issue", type=int, required=True, help="Open GitHub issue number")
    parser.add_argument("--slug", help="Short branch/worktree slug; issue title is used when omitted")
    parser.add_argument("--base", default="main", help="Base branch (default: main)")
    parser.add_argument("--remote", default="origin", help="Git remote (default: origin)")
    parser.add_argument("--worktrees-dir", default=".worktrees", help="Directory under the primary checkout")
    args = parser.parse_args()

    try:
        require_command("git")
        repo = resolve_repo(args.repo)

        # Refuse to run from a linked worktree. The primary checkout is the first worktree entry.
        worktree_lines = git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
        primary = next((line.split(" ", 1)[1] for line in worktree_lines if line.startswith("worktree ")), None)
        if primary and Path(primary).resolve() != repo:
            raise RuntimeError(f"--repo must point to the primary checkout: {primary}")

        slug = slugify(args.slug or issue_title(repo, args.issue))
        branch = f"issue/{args.issue}-{slug}"
        worktrees_dir = (repo / args.worktrees_dir).resolve()
        worktree = worktrees_dir / f"issue-{args.issue}-{slug}"

        if worktree.exists():
            raise RuntimeError(f"worktree path already exists: {worktree}")
        if ref_exists(repo, f"refs/heads/{branch}"):
            raise RuntimeError(f"local branch already exists: {branch}")
        if remote_branch_exists(repo, args.remote, branch):
            raise RuntimeError(f"remote branch already exists: {args.remote}/{branch}")

        git(repo, "fetch", args.remote, args.base)
        base_ref = f"{args.remote}/{args.base}"
        base_sha = git(repo, "rev-parse", base_ref).stdout.strip()

        worktrees_dir.mkdir(parents=True, exist_ok=True)
        relative_dir = Path(args.worktrees_dir).as_posix().rstrip("/") + "/"
        add_local_exclude(repo, relative_dir)

        git(repo, "worktree", "add", "-b", branch, str(worktree), base_ref)

        result = {
            "status": "created",
            "issue": args.issue,
            "repo": str(repo),
            "base": args.base,
            "base_ref": base_ref,
            "base_sha": base_sha,
            "branch": branch,
            "worktree": str(worktree),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (RuntimeError, CommandError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

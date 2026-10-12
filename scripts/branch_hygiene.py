"""List worktrees and branches whose pull request has merged, so they are removed the same day (AGENTS.md section 8,
"Each agent and each session works in its own worktree").

    python scripts/branch_hygiene.py            # report only: nothing changes
    python scripts/branch_hygiene.py --prune    # remove merged worktrees that are clean, delete merged remote branches

This repository squash-merges, so git can't tell a merged branch from an unmerged one (the branch's commits are never on
``main``). "Merged" here means: the branch's tip is the head commit of a pull request GitHub merged, and no pull request
from it is open. A branch name reused after its merge, with newer commits on it, is therefore kept. Never touched, only
reported: a worktree with uncommitted work, a branch with an open pull request, a branch with no pull request (it may be
someone's work in progress), ``main`` and the main checkout. Needs ``git`` and an authenticated ``gh``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from dataclasses import dataclass, field

REPO = "colombod-personal/the-vault"


def run(*args: str, check: bool = True) -> str:
    return subprocess.run(args, check=check, capture_output=True, text=True, encoding="utf-8").stdout


@dataclass(frozen=True)
class Merged:
    number: int
    merged_at: datetime.datetime


def pull_requests() -> tuple[dict[str, Merged], set[str]]:
    """Merged pull requests by the head commit they shipped, and the head branches of open ones."""
    def heads(state: str) -> list[dict]:
        return json.loads(run("gh", "pr", "list", "--repo", REPO, "--state", state, "--limit", "1000",
                              "--json", "number,headRefName,headRefOid,mergedAt"))
    merged = {p["headRefOid"]: Merged(p["number"], datetime.datetime.fromisoformat(p["mergedAt"].replace("Z", "+00:00")))
              for p in heads("merged")}
    return merged, {p["headRefName"] for p in heads("open")}


def tip(ref: str) -> str:
    return run("git", "rev-parse", "--verify", "--quiet", ref, check=False).strip()


@dataclass
class Worktree:
    path: str
    branch: str | None  # None when detached
    dirty: int = 0
    main_checkout: bool = False


def worktrees() -> list[Worktree]:
    out, current = [], None
    for line in run("git", "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = Worktree(path=line.split(" ", 1)[1], branch=None, main_checkout=not out)
            out.append(current)
        elif line.startswith("branch ") and current:
            current.branch = line.split(" ", 1)[1].removeprefix("refs/heads/")
    for wt in out:
        wt.dirty = len(run("git", "-C", wt.path, "status", "--porcelain", check=False).splitlines())
    return out


def remote_branches() -> list[str]:
    names = run("git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").split()
    return sorted(n.removeprefix("origin/") for n in names if n not in ("origin", "origin/HEAD", "origin/main"))


@dataclass
class Report:
    remove_worktrees: list[Worktree] = field(default_factory=list)
    keep_worktrees: list[tuple[Worktree, str]] = field(default_factory=list)
    delete_branches: list[tuple[str, int]] = field(default_factory=list)
    keep_branches: list[tuple[str, str]] = field(default_factory=list)


def classify(wts: list[Worktree], branches: list[str], merged: dict[str, Merged], open_heads: set[str],
             tips: dict[str, str], now: datetime.datetime, min_age: datetime.timedelta) -> Report:
    """Decide what to remove. ``tips`` maps ``refs/heads/<b>`` and ``refs/remotes/origin/<b>`` to commit ids. A worktree
    merged less than ``min_age`` ago is its own session's to remove (AGENTS.md section 8), so it is only reported."""
    r = Report()
    for wt in wts:
        if wt.main_checkout:
            r.keep_worktrees.append((wt, "main checkout"))
        elif wt.dirty:
            r.keep_worktrees.append((wt, f"uncommitted work ({wt.dirty} files): report in its bead"))
        elif wt.branch is None:
            r.keep_worktrees.append((wt, "detached: check its bead"))
        elif wt.branch in open_heads:
            r.keep_worktrees.append((wt, "open pull request"))
        elif (m := merged.get(tips.get(f"refs/heads/{wt.branch}", ""))) and now - m.merged_at < min_age:
            r.keep_worktrees.append((wt, f"PR #{m.number} merged {m.merged_at:%Y-%m-%d %H:%M}Z, under a day ago: its session removes it"))
        elif m:
            r.remove_worktrees.append(wt)
        else:
            r.keep_worktrees.append((wt, "not a merged pull request's head: work in progress?"))
    for b in branches:
        if b in open_heads:
            r.keep_branches.append((b, "open pull request"))
        elif m := merged.get(tips.get(f"refs/remotes/origin/{b}", "")):
            r.delete_branches.append((b, m.number))
        else:
            r.keep_branches.append((b, "not a merged pull request's head: check beads before deleting"))
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prune", action="store_true", help="remove merged clean worktrees and delete merged remote branches")
    ap.add_argument("--min-age-hours", type=float, default=24,
                    help="leave worktrees merged more recently than this to their own session (default 24)")
    args = ap.parse_args(argv)

    run("git", "fetch", "--prune", "origin")
    merged, open_heads = pull_requests()
    wts = worktrees()
    branches = remote_branches()
    refs = [f"refs/heads/{wt.branch}" for wt in wts if wt.branch] + [f"refs/remotes/origin/{b}" for b in branches]
    tips = {ref: tip(ref) for ref in refs}
    now = datetime.datetime.now(datetime.timezone.utc)
    r = classify(wts, branches, merged, open_heads, tips, now, datetime.timedelta(hours=args.min_age_hours))

    print(f"Worktrees: {len(wts)}; merged and clean: {len(r.remove_worktrees)}")
    for wt in r.remove_worktrees:
        print(f"  remove  {wt.path}  ({wt.branch}, PR #{merged[tips[f'refs/heads/{wt.branch}']].number} merged)")
    for wt, why in r.keep_worktrees:
        print(f"  keep    {wt.path}  ({wt.branch or 'detached'}): {why}")
    print(f"Remote branches: {len(branches)}; merged: {len(r.delete_branches)}; kept: {len(r.keep_branches)}")
    for b, n in r.delete_branches:
        print(f"  delete  {b}  (PR #{n} merged)")
    for b, why in r.keep_branches:
        print(f"  keep    {b}: {why}")

    if not args.prune:
        print("Report only. Run with --prune to remove the merged, clean ones.")
        return 0
    for wt in r.remove_worktrees:
        run("git", "worktree", "remove", wt.path)
        run("git", "branch", "-D", wt.branch, check=False)
    for b, _ in r.delete_branches:
        run("git", "push", "origin", "--delete", b, check=False)
    run("git", "fetch", "--prune", "origin")
    print(f"Pruned: {len(r.remove_worktrees)} worktrees, {len(r.delete_branches)} remote branches. "
          f"Now: {len(worktrees())} worktrees, {len(remote_branches())} remote branches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

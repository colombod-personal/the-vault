"""List worktrees and branches whose pull request has merged, so they are removed the same day (AGENTS.md section 8,
"Each agent and each session works in its own worktree").

    python scripts/branch_hygiene.py            # report only: changes nothing, not even a fetch
    python scripts/branch_hygiene.py --fetch    # the same report after `git fetch --prune origin`
    python scripts/branch_hygiene.py --prune    # fetch, then remove the merged ones listed under "remove"/"delete"

This repository squash-merges, so git can't tell a merged branch from an unmerged one (the branch's commits are never on
``main``). "Merged" here means: the branch's tip is the head commit of a pull request GitHub merged, and no pull request
from it is open. A branch name reused after its merge, with newer commits on it, is therefore kept.

A worktree is removed only when its branch is merged, more than ``--min-age-hours`` ago (24 by default: a session removes
its own on the day), and nothing could be lost or interrupted. Kept and reported instead: the main checkout; the worktree
this script runs in; a locked worktree (``git worktree lock``); one whose ``git status`` fails; one with uncommitted or
untracked files, or ignored files that are not rebuildable caches (``.env.local``, say); a detached one; one with an open
pull request; one whose branch isn't a merged pull request's head. A remote branch is deleted only when merged and no kept
worktree has that branch or has its commit checked out (detached). Every removal is tied to the commit that was reported (a lease): a commit or push made since then
makes git refuse, and the branch is kept and reported, never counted as pruned. If ``gh`` fails, nothing changes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = "colombod-personal/the-vault"
# Ignored files that are rebuilt by a command, so removing them loses nothing.
# Folders git ignores that a build or test run makes again, at any depth (vault/__pycache__/ as well as __pycache__/).
REBUILDABLE = {".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build"}


def rebuildable(path: str) -> bool:
    """An entry of ``git status --ignored=matching`` that a build or test run makes again: an ignored folder (listed with
    a trailing "/") whose own name is a cache's, or a .pyc / .coverage file. A file inside a tracked folder that happens
    to be called build (docs/build/.env.local) is not a cache, nor is a file named __pycache__."""
    path = path.replace("\\", "/")
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return (path.endswith("/") and name in REBUILDABLE) or name == ".coverage" or path.endswith(".pyc")


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")


@dataclass(frozen=True)
class Merged:
    number: int
    merged_at: datetime.datetime


def pull_requests() -> tuple[dict[str, Merged], set[str]]:
    """Merged pull requests by the head commit they shipped, and the head branches of open ones. Raises if ``gh``
    fails, so a missing list can never look like "nothing is open"."""
    def heads(state: str) -> list[dict]:
        out = subprocess.run(["gh", "pr", "list", "--repo", REPO, "--state", state, "--limit", "1000",
                              "--json", "number,headRefName,headRefOid,mergedAt"],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        return json.loads(out)
    merged = {p["headRefOid"]: Merged(p["number"], datetime.datetime.fromisoformat(p["mergedAt"].replace("Z", "+00:00")))
              for p in heads("merged")}
    return merged, {p["headRefName"] for p in heads("open")}


def tip(ref: str, cwd: str | None = None) -> str:
    return git("rev-parse", "--verify", "--quiet", ref, cwd=cwd).stdout.strip()


def delete_remote_branch(branch: str, expected: str, remote: str = "origin", cwd: str | None = None) -> bool:
    """Delete ``branch`` on ``remote`` only while it still points at ``expected`` (the commit that was classified as
    merged): a push made since then makes git refuse, so nobody's new commits are deleted."""
    ref = f"refs/heads/{branch}"
    return git("push", f"--force-with-lease={ref}:{expected}", remote, f":{ref}", cwd=cwd).returncode == 0


@dataclass
class Worktree:
    path: str
    branch: str | None  # None when detached
    head: str = ""  # the commit checked out
    main_checkout: bool = False
    locked: bool = False
    current: bool = False  # the worktree this script runs in
    unsafe: str = ""  # why its files can't be removed safely; empty when clean


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def local_state(path: str) -> str:
    """Why removing this worktree's files could lose something, or "" when nothing can be lost."""
    status = git("--no-optional-locks", "status", "--porcelain", "--ignored=matching", cwd=path)
    if status.returncode:
        return f"git status failed ({status.returncode}): left alone"
    tracked = [l for l in status.stdout.splitlines() if not l.startswith("!! ")]
    if tracked:
        return f"uncommitted or untracked work ({len(tracked)} files)"
    ignored = [l[3:] for l in status.stdout.splitlines() if l.startswith("!! ")]
    kept = [p for p in ignored if not rebuildable(p)]
    if kept:
        return f"ignored files that are not caches ({', '.join(kept[:3])}{'...' if len(kept) > 3 else ''})"
    return ""


def worktrees(cwd: str | None = None) -> list[Worktree]:
    here = git("rev-parse", "--show-toplevel", cwd=cwd).stdout.strip()
    out: list[Worktree] = []
    listing = git("worktree", "list", "--porcelain", cwd=cwd)
    if listing.returncode:
        raise RuntimeError(f"git worktree list failed: {listing.stderr.strip()}")
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            out.append(Worktree(path=line.split(" ", 1)[1], branch=None, main_checkout=not out))
        elif line.startswith("HEAD ") and out:
            out[-1].head = line.split(" ", 1)[1]
        elif line.startswith("branch ") and out:
            out[-1].branch = line.split(" ", 1)[1].removeprefix("refs/heads/")
        elif (line == "locked" or line.startswith("locked ")) and out:
            out[-1].locked = True
    for wt in out:
        wt.current = bool(here) and same_path(wt.path, here)
        wt.unsafe = local_state(wt.path) if Path(wt.path).is_dir() else "missing on disk: run `git worktree prune`"
    return out


def remote_branches(cwd: str | None = None) -> list[str]:
    names = git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin", cwd=cwd).stdout.split()
    return sorted(n.removeprefix("origin/") for n in names if n not in ("origin", "origin/HEAD", "origin/main"))


@dataclass
class Report:
    remove_worktrees: list[Worktree] = field(default_factory=list)
    keep_worktrees: list[tuple[Worktree, str]] = field(default_factory=list)
    delete_branches: list[tuple[str, int]] = field(default_factory=list)
    keep_branches: list[tuple[str, str]] = field(default_factory=list)


def classify(wts: list[Worktree], branches: list[str], merged: dict[str, Merged], open_heads: set[str],
             tips: dict[str, str], now: datetime.datetime, min_age: datetime.timedelta) -> Report:
    """Decide what to remove. ``tips`` maps ``refs/heads/<b>`` and ``refs/remotes/origin/<b>`` to commit ids."""
    r = Report()
    for wt in wts:
        m = merged.get(tips.get(f"refs/heads/{wt.branch}", "")) if wt.branch else None
        if wt.main_checkout:
            why = "main checkout"
        elif wt.current:
            why = "this script runs in it"
        elif wt.locked:
            why = "locked (git worktree lock)"
        elif wt.unsafe:
            why = f"{wt.unsafe}: report in its bead"
        elif wt.branch is None:
            why = "detached: check its bead"
        elif wt.branch in open_heads:
            why = "open pull request"
        elif not m:
            why = "not a merged pull request's head: work in progress?"
        elif now - m.merged_at < min_age:
            why = f"PR #{m.number} merged {m.merged_at:%Y-%m-%d %H:%M}Z, under {min_age.total_seconds() / 3600:g} h ago: its session removes it"
        else:
            r.remove_worktrees.append(wt)
            continue
        r.keep_worktrees.append((wt, why))
    # A kept worktree keeps its branch on the remote too: by name, or (detached) by the commit it has checked out.
    in_use = {wt.branch for wt, _ in r.keep_worktrees if wt.branch}
    in_use_heads = {wt.head for wt, _ in r.keep_worktrees if wt.head}
    for b in branches:
        m = merged.get(tips.get(f"refs/remotes/origin/{b}", ""))
        if b == "main":
            r.keep_branches.append((b, "main"))
        elif b in open_heads:
            r.keep_branches.append((b, "open pull request"))
        elif b in in_use or tips.get(f"refs/remotes/origin/{b}") in in_use_heads:
            r.keep_branches.append((b, "a kept worktree has this branch"))
        elif m:
            r.delete_branches.append((b, m.number))
        else:
            r.keep_branches.append((b, "not a merged pull request's head: check beads before deleting"))
    return r


def prune(r: Report, tips: dict[str, str], cwd: str | None = None) -> tuple[int, int, list[str]]:
    """Remove what ``classify`` listed, each re-checked against the reported commit and state just before."""
    removed, deleted, refused = 0, 0, []
    for wt in r.remove_worktrees:
        ref = f"refs/heads/{wt.branch}"
        if tip(ref, cwd) != tips[ref]:
            refused.append(f"worktree {wt.path}: its branch moved since the report, kept")
            continue
        if (state := local_state(wt.path)):
            refused.append(f"worktree {wt.path}: {state} since the report, kept")
            continue
        done = git("worktree", "remove", wt.path, cwd=cwd)  # no --force: git itself refuses if anything changed
        if done.returncode:
            refused.append(f"worktree {wt.path}: git refused ({done.stderr.strip()[:80]}), kept")
            continue
        removed += 1
        if git("update-ref", "-d", ref, tips[ref], cwd=cwd).returncode:
            refused.append(f"local branch {wt.branch}: moved since the report, kept")
    for b, _ in r.delete_branches:
        if delete_remote_branch(b, tips[f"refs/remotes/origin/{b}"], cwd=cwd):
            deleted += 1
        else:
            refused.append(f"remote branch {b}: pushed to since the report (or the push failed), kept")
    return removed, deleted, refused


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="git fetch --prune origin before the report")
    ap.add_argument("--prune", action="store_true", help="remove what the report lists (fetches first)")
    ap.add_argument("--min-age-hours", type=float, default=24,
                    help="leave worktrees merged more recently than this to their own session (default 24)")
    args = ap.parse_args(argv)

    if args.fetch or args.prune:
        if git("fetch", "--prune", "origin").returncode:
            print("git fetch failed: nothing changed", file=sys.stderr)
            return 2
    try:
        merged, open_heads = pull_requests()
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        print(f"could not read pull requests from GitHub ({e}): nothing changed", file=sys.stderr)
        return 2
    wts = worktrees()
    branches = remote_branches()
    refs = [f"refs/heads/{wt.branch}" for wt in wts if wt.branch] + [f"refs/remotes/origin/{b}" for b in branches]
    tips = {ref: tip(ref) for ref in refs}
    now = datetime.datetime.now(datetime.timezone.utc)
    r = classify(wts, branches, merged, open_heads, tips, now, datetime.timedelta(hours=args.min_age_hours))

    print(f"{now:%Y-%m-%d %H:%M}Z  worktrees: {len(wts)}; to remove: {len(r.remove_worktrees)}")
    for wt in r.remove_worktrees:
        print(f"  remove  {wt.path}  ({wt.branch}, PR #{merged[tips[f'refs/heads/{wt.branch}']].number} merged)")
    for wt, why in r.keep_worktrees:
        print(f"  keep    {wt.path}  ({wt.branch or 'detached'}): {why}")
    print(f"Remote branches: {len(branches)}; to delete: {len(r.delete_branches)}; kept: {len(r.keep_branches)}")
    for b, n in r.delete_branches:
        print(f"  delete  {b}  (PR #{n} merged)")
    for b, why in r.keep_branches:
        print(f"  keep    {b}: {why}")

    if not args.prune:
        print("Report only: nothing changed. --prune removes what is listed under remove/delete.")
        return 0
    removed, deleted, refused = prune(r, tips)
    git("fetch", "--prune", "origin")
    for line in refused:
        print(f"  refused {line}")
    print(f"Pruned: {removed} worktrees, {deleted} remote branches; refused: {len(refused)}. "
          f"Now: {len(worktrees())} worktrees, {len(remote_branches())} remote branches.")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())

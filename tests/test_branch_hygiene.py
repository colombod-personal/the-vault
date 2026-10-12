"""scripts/branch_hygiene.py removes only what a merged pull request shipped and nobody is still working on
(AGENTS.md section 8, "Each agent and each session works in its own worktree"). The tests build a real repository with
a bare origin and one worktree per case; only ``gh``'s answer (the merged and open pull requests) is made up."""

import datetime
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import branch_hygiene as hygiene  # noqa: E402

NOW = datetime.datetime.now(datetime.timezone.utc)
OLD = NOW - datetime.timedelta(days=3)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A bare origin, a main checkout and worktrees for every case. Returns (main checkout, {case: worktree path},
    {case: commit}) where the commit is what the case's branch points at."""
    origin, main = tmp_path / "origin.git", tmp_path / "main"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("clone", "-q", str(origin), str(main), cwd=tmp_path)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git("config", k, v, cwd=main)
    (main / ".gitignore").write_text(".env*\nnode_modules/\n__pycache__\n")  # no slash: a file of that name too
    git("add", ".gitignore", cwd=main)
    git("commit", "-q", "-m", "base", cwd=main)
    git("push", "-q", "origin", "main", cwd=main)
    paths, tips = {}, {"main": git("rev-parse", "HEAD", cwd=main)}
    (main / "docs" / "build").mkdir(parents=True)  # a tracked folder that happens to be called build
    (main / "docs" / "build" / "index.md").write_text("tracked")
    git("add", "docs", cwd=main)
    git("commit", "-q", "-m", "docs/build is tracked", cwd=main)
    git("push", "-q", "origin", "main", cwd=main)
    for case in ("merged", "fresh", "dirty", "untracked", "env", "cache", "locked", "detached", "open", "unmerged",
                 "here", "kept_remote", "env_in_build", "file_named_cache"):
        path = tmp_path / f"wt-{case}"
        git("worktree", "add", "-q", "-b", f"b/{case}", str(path), "origin/main", cwd=main)
        git("commit", "-q", "--allow-empty", "-m", case, cwd=path)
        git("push", "-q", "origin", f"b/{case}", cwd=path)
        paths[case], tips[case] = path, git("rev-parse", "HEAD", cwd=path)
    (paths["dirty"] / ".gitignore").write_text("changed\n")
    (paths["untracked"] / "notes.txt").write_text("mine")
    (paths["env"] / ".env.local").write_text("SECRET=1")
    (paths["env_in_build"] / "docs" / "build" / ".env.local").write_text("SECRET=1")  # under a tracked "build"
    (paths["file_named_cache"] / "pkg").mkdir()
    (paths["file_named_cache"] / "pkg" / "__pycache__").write_text("SECRET=1")
    (paths["cache"] / "node_modules").mkdir()
    (paths["cache"] / "node_modules" / "x.js").write_text("")
    (paths["cache"] / "pkg" / "__pycache__").mkdir(parents=True)  # nested: still a cache
    (paths["cache"] / "pkg" / "__pycache__" / "m.cpython-312.pyc").write_text("")
    git("worktree", "lock", str(paths["locked"]), cwd=main)
    git("checkout", "-q", "--detach", cwd=paths["detached"])
    git("fetch", "-q", "origin", cwd=main)
    return main, paths, tips


def gh_answer(tips, **when):
    """Merged pull requests for every case's tip (merged long ago unless ``when`` says otherwise), the main checkout's
    tip too (so only its guard keeps it), and an open one for ``b/open``."""
    merged = {tips[case]: hygiene.Merged(n, when.get(case, OLD)) for n, case in enumerate(tips, start=100)
              if case not in ("unmerged",)}
    return merged, {"b/open"}


def report(main, tips, monkeypatch, cwd=None, **when):
    monkeypatch.chdir(cwd or main)
    merged, open_heads = gh_answer(tips, **when)
    wts = hygiene.worktrees()
    branches = hygiene.remote_branches()
    refs = [f"refs/heads/{wt.branch}" for wt in wts if wt.branch] + [f"refs/remotes/origin/{b}" for b in branches]
    ref_tips = {ref: hygiene.tip(ref) for ref in refs}
    return hygiene.classify(wts, branches, merged, open_heads, ref_tips, NOW, datetime.timedelta(hours=24)), ref_tips


def test_only_the_merged_clean_old_worktree_is_removed(repo, monkeypatch):
    main, paths, tips = repo
    r, _ = report(main, tips, monkeypatch, cwd=paths["here"], fresh=NOW - datetime.timedelta(hours=2))
    assert [Path(wt.path).name for wt in r.remove_worktrees] == ["wt-cache", "wt-kept_remote", "wt-merged"]
    why = {Path(wt.path).name: reason for wt, reason in r.keep_worktrees}
    assert why["main"] == "main checkout"  # its tip is a merged PR's head here, so only this guard keeps it
    assert why["wt-here"] == "this script runs in it"
    assert why["wt-locked"].startswith("locked")
    assert why["wt-detached"].startswith("detached")
    assert why["wt-open"] == "open pull request"
    assert why["wt-unmerged"].startswith("not a merged")
    assert "under 24 h" in why["wt-fresh"]  # its own session removes it
    assert why["wt-dirty"].startswith("uncommitted")
    assert why["wt-untracked"].startswith("uncommitted or untracked")
    assert ".env.local" in why["wt-env"]  # ignored but not a cache: would be lost
    assert "wt-cache" not in why  # node_modules is rebuildable
    assert "docs/build/.env.local" in why["wt-env_in_build"]  # an ancestor named build does not make it a cache
    assert "pkg/__pycache__" in why["wt-file_named_cache"]  # a FILE named __pycache__ is not a cache folder


def test_main_and_the_branches_of_kept_worktrees_are_never_deleted(repo, monkeypatch):
    main, paths, tips = repo
    r, _ = report(main, tips, monkeypatch, cwd=paths["here"], fresh=NOW - datetime.timedelta(hours=2))
    assert "main" not in hygiene.remote_branches()
    deleted = {b for b, _ in r.delete_branches}
    assert deleted == {"b/cache", "b/kept_remote", "b/merged"}  # every other branch belongs to a kept worktree
    assert {b for b, _ in r.keep_branches} >= {"b/here", "b/dirty", "b/env", "b/locked", "b/fresh", "b/open",
                                                "b/detached"}  # detached: kept by the commit it has checked out


def test_prune_removes_only_what_was_reported_and_refuses_what_moved(repo, monkeypatch):
    main, paths, tips = repo
    r, ref_tips = report(main, tips, monkeypatch, cwd=paths["here"])
    # After the report: someone pushes to b/kept_remote's remote branch, and commits in wt-cache.
    other = paths["kept_remote"]
    git("commit", "-q", "--allow-empty", "-m", "late", cwd=other)
    git("push", "-q", "origin", "b/kept_remote", cwd=other)
    git("commit", "-q", "--allow-empty", "-m", "late", cwd=paths["cache"])

    removed, deleted, refused = hygiene.prune(r, ref_tips)
    assert not paths["merged"].exists() and paths["here"].exists() and paths["env"].exists()
    assert paths["cache"].exists() and any("wt-cache" in line and "moved" in line for line in refused)
    remote = git("ls-remote", "--heads", "origin", cwd=main)
    assert "refs/heads/b/merged" not in remote and "refs/heads/b/cache" not in remote
    assert "refs/heads/b/kept_remote" in remote  # the late push survives
    assert any("b/kept_remote" in line for line in refused)
    assert (paths["env"] / ".env.local").read_text() == "SECRET=1"


def test_report_mode_changes_nothing_and_a_gh_failure_stops_everything(repo, monkeypatch, capsys):
    main, paths, tips = repo
    monkeypatch.chdir(paths["here"])
    before = (git("worktree", "list", cwd=main), git("ls-remote", "origin", cwd=main))
    monkeypatch.setattr(hygiene, "pull_requests", lambda: gh_answer(tips))
    assert hygiene.main([]) == 0 and "nothing changed" in capsys.readouterr().out

    def broken():
        raise subprocess.CalledProcessError(1, "gh")
    monkeypatch.setattr(hygiene, "pull_requests", broken)
    assert hygiene.main(["--prune"]) == 2
    assert (git("worktree", "list", cwd=main), git("ls-remote", "origin", cwd=main)) == before


def test_a_remote_branch_pushed_to_after_the_report_is_not_deleted(repo):
    main, paths, tips = repo
    other = paths["unmerged"]
    git("commit", "-q", "--allow-empty", "-m", "pushed after the report", cwd=other)
    git("push", "-q", "origin", "b/unmerged", cwd=other)
    assert hygiene.delete_remote_branch("b/unmerged", tips["unmerged"], cwd=main) is False
    assert hygiene.delete_remote_branch("b/merged", tips["merged"], cwd=main) is True

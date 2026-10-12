"""scripts/branch_hygiene.py removes only what a merged pull request shipped and nobody is still working on
(AGENTS.md section 8, "Each agent and each session works in its own worktree")."""

import datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import branch_hygiene as hygiene  # noqa: E402

NOW = datetime.datetime(2026, 10, 12, 12, tzinfo=datetime.timezone.utc)
DAY = datetime.timedelta(hours=24)


def merged(number, hours_ago):
    return hygiene.Merged(number, NOW - datetime.timedelta(hours=hours_ago))


def test_only_merged_clean_old_worktrees_are_removed():
    wts = [
        hygiene.Worktree("C:/Repos/the-vault", None, main_checkout=True),
        hygiene.Worktree("C:/Repos/old", "fix/old"),
        hygiene.Worktree("C:/Repos/today", "fix/today"),
        hygiene.Worktree("C:/Repos/dirty", "fix/dirty", dirty=3),
        hygiene.Worktree("C:/Repos/reused", "fix/reused"),
        hygiene.Worktree("C:/Repos/open", "fix/open"),
        hygiene.Worktree("C:/Repos/wip", "fix/wip"),
        hygiene.Worktree("C:/Repos/detached", None),
    ]
    tips = {"refs/heads/fix/old": "a1", "refs/heads/fix/today": "b2", "refs/heads/fix/dirty": "c3",
            "refs/heads/fix/reused": "newer", "refs/heads/fix/open": "e5", "refs/heads/fix/wip": "f6"}
    shipped = {"a1": merged(1, 48), "b2": merged(2, 3), "c3": merged(3, 48), "d4": merged(4, 48), "e5": merged(5, 48)}
    r = hygiene.classify(wts, [], shipped, {"fix/open"}, tips, NOW, DAY)
    assert [wt.path for wt in r.remove_worktrees] == ["C:/Repos/old"]
    kept = {wt.path: why for wt, why in r.keep_worktrees}
    assert "under a day ago" in kept["C:/Repos/today"]  # its own session removes it
    assert "uncommitted" in kept["C:/Repos/dirty"]
    assert "not a merged" in kept["C:/Repos/reused"]  # name reused after the merge, newer commits on it
    assert kept["C:/Repos/open"] == "open pull request"
    assert set(kept) == {wt.path for wt in wts} - {"C:/Repos/old"}


def test_a_remote_branch_is_deleted_only_when_its_tip_was_merged_and_nothing_is_open():
    tips = {"refs/remotes/origin/done": "a1", "refs/remotes/origin/reused": "newer", "refs/remotes/origin/open": "c3",
            "refs/remotes/origin/wip": "d4"}
    r = hygiene.classify([], ["done", "open", "reused", "wip"], {"a1": merged(10, 1), "c3": merged(11, 48)},
                         {"open"}, tips, NOW, DAY)
    assert r.delete_branches == [("done", 10)]  # no age rule for remote branches: the local worktree keeps its own
    assert [b for b, _ in r.keep_branches] == ["open", "reused", "wip"]


def test_a_remote_branch_pushed_to_after_the_report_is_not_deleted(tmp_path):
    def git(*args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    git("clone", "-q", str(remote), str(work), cwd=tmp_path)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git("config", k, v, cwd=work)
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    git("push", "-q", "origin", "HEAD:refs/heads/done", "HEAD:refs/heads/moved", cwd=work)
    reported = git("rev-parse", "HEAD", cwd=work)
    git("commit", "-q", "--allow-empty", "-m", "pushed after the report", cwd=work)
    git("push", "-q", "origin", "HEAD:refs/heads/moved", cwd=work)

    assert hygiene.delete_remote_branch("moved", reported, cwd=work) is False  # refused: the new commit survives
    assert hygiene.delete_remote_branch("done", reported, cwd=work) is True
    assert git("ls-remote", "--heads", "origin", cwd=work).split()[1::2] == ["refs/heads/moved"]

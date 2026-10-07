"""Apply the label renames decided in docs/triage.md ("Label decisions", issue #131) to existing issues and pull requests.

    python scripts/relabel_issues.py                 # dry run (the default): prints the exact list, changes nothing
    python scripts/relabel_issues.py --apply         # runs `gh issue edit` / `gh pr edit` for every line printed
    python scripts/relabel_issues.py --apply --delete-old-labels   # afterwards, also deletes the emptied old labels

Dry run is the default because AGENTS.md says not to change many issues at once without showing the owner the list first.
Needs the GitHub CLI (``gh``) signed in with write access to the repository; the dry run only reads.

A rename adds the new label (unless the item already has it) and removes the old one, so an item that carries both
(for example #62 with ``data`` and ``area:data``) ends with just the new one. ``in-progress`` is not renamed: see the decisions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

REPO = "colombod-personal/the-vault"

# old label -> new label. The decisions, with reasons, are in docs/triage.md.
RENAMES = {
    "enhancement": "type:feature",
    "data": "area:data",
}


def gh(*args: str) -> str:
    done = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def fetch(run, kind: str, label: str, repo: str) -> list[dict]:
    """Every issue or pull request (open and closed) that carries ``label``."""
    out = run(kind, "list", "-R", repo, "--state", "all", "--label", label, "--limit", "1000",
             "--json", "number,title,state,labels")
    return json.loads(out)


def plan(items: list[dict], renames: dict[str, str], kind: str) -> list[dict]:
    """One entry per item that needs a change: what to add and what to remove. Pure, so it is tested without GitHub."""
    changes: dict[int, dict] = {}
    for item in items:
        names = {label["name"] for label in item["labels"]}
        add = sorted({new for old, new in renames.items() if old in names and new not in names})
        remove = sorted(old for old in renames if old in names)
        if remove:
            changes[item["number"]] = {"kind": kind, "number": item["number"], "state": item["state"], "title": item["title"],
                                       "add": add, "remove": remove}
    return [changes[n] for n in sorted(changes)]


def command(change: dict, repo: str) -> list[str]:
    cmd = [change["kind"], "edit", str(change["number"]), "-R", repo]
    for label in change["add"]:
        cmd += ["--add-label", label]
    for label in change["remove"]:
        cmd += ["--remove-label", label]
    return cmd


def render(changes: list[dict]) -> str:
    if not changes:
        return "Nothing to change: no issue or pull request carries an old label."
    lines = [f"{len(changes)} item(s) would change:"]
    for c in changes:
        what = "issue" if c["kind"] == "issue" else "pull request"
        lines.append(f"  {what} #{c['number']} [{c['state']}] {c['title'][:70]}: add {c['add'] or '-'}, remove {c['remove']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, run=gh) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--apply", action="store_true", help="make the changes (default: dry run, print the list only)")
    parser.add_argument("--delete-old-labels", action="store_true", help="with --apply: delete the old labels afterwards")
    args = parser.parse_args(argv)
    if args.delete_old_labels and not args.apply:
        parser.error("--delete-old-labels needs --apply")

    changes: list[dict] = []
    for kind in ("issue", "pr"):
        items: list[dict] = []
        for old in RENAMES:
            items += fetch(run, kind, old, args.repo)
        changes += plan(items, RENAMES, kind)
    print(render(changes))
    if not args.apply:
        print("\nDry run: nothing was changed. Run again with --apply after the owner has seen this list.")
        return 0

    existing = {row["name"] for row in json.loads(run("label", "list", "-R", args.repo, "--limit", "200", "--json", "name"))}
    missing = sorted(set(RENAMES.values()) - existing)
    if missing:
        print(f"Refusing to apply: the new label(s) {missing} do not exist in {args.repo}", file=sys.stderr)
        return 1
    for change in changes:
        run(*command(change, args.repo))
        print(f"done: {change['kind']} #{change['number']}")
    if args.delete_old_labels:
        for old in RENAMES:
            if old in existing:
                run("label", "delete", old, "-R", args.repo, "--yes")
                print(f"deleted label {old}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Keep every issue's progress real, in the issue itself (AGENTS.md section 8).

An issue carries a ``Progress`` block between two markers in its body: the state in one line (planned, in review, merged,
deployed, verified), the pull requests, and one row per acceptance criterion with its state and the evidence. People read the issue;
this script is how the block is written, so it is always in the same shape and never from memory.

    python scripts/issue_progress.py show 246
    python scripts/issue_progress.py pr-merged --pr 273        # what the workflow runs when a PR merges
    python scripts/issue_progress.py tick 246 "Disconnect" --evidence "https://github.com/.../issues/246#issuecomment-1"
    python scripts/issue_progress.py state 246 verified
    python scripts/issue_progress.py set 246 --file rows.json   # criteria rows: [{"criterion", "state", "evidence"}]

States of a criterion: ``open``, ``in review`` (a PR is open), ``merged`` (code is on main, not seen working), ``verified``
(evidence in the issue), ``owner`` (waits for the owner: a decision, an action, a real-app check), ``waived`` (the owner
dropped it, with the comment that says so). The issue state is derived: verified only when every row is verified or waived.
Closing is a separate, deliberate act: ``close`` refuses unless every row is verified or waived.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import tempfile

START = "<!-- progress:start (kept by scripts/issue_progress.py: do not edit by hand) -->"
END = "<!-- progress:end -->"
STATES = ("open", "in review", "merged", "verified", "owner", "waived")
DONE = {"verified", "waived"}
REFS = re.compile(r"(?im)^\s*(?:Refs|Closes|Fixes|Resolves)\s+((?:#\d+[\s,]*(?:and\s+)?)+)")
CLOSES = re.compile(r"(?im)^\s*(?:Closes|Fixes|Resolves)\s+((?:#\d+[\s,]*(?:and\s+)?)+)")


def clean(text: str) -> str:
    return " ".join(str(text).replace("|", "/").split())


def derive(rows: list[dict], prs: dict[int, str]) -> str:
    """One line a person can trust: what exists, what was seen working, what is left and who is waiting."""
    if rows and all(r["state"] in DONE for r in rows):
        return "VERIFIED: every criterion has its evidence"
    left = [r for r in rows if r["state"] not in DONE]
    owner = sum(r["state"] == "owner" for r in left)
    open_prs = [n for n, s in prs.items() if s == "open"]
    merged_prs = [n for n, s in prs.items() if s == "merged"]
    parts = []
    if open_prs:
        parts.append("IN REVIEW (" + ", ".join(f"#{n}" for n in open_prs) + " open)")
    if merged_prs:
        parts.append("MERGED (" + ", ".join(f"#{n}" for n in merged_prs) + ") but not verified")
    if not parts:
        parts.append("NOT STARTED" if not rows or all(r["state"] == "open" for r in rows) else "PARTLY DONE")
    parts.append(f"{len(left)} of {len(rows)} criteria still open" if rows else "no criteria listed")
    if owner:
        parts.append(f"{owner} wait for the owner")
    return " · ".join(parts)


def render(rows: list[dict], prs: dict[int, str], today: str | None = None) -> str:
    today = today or datetime.date.today().isoformat()
    lines = [START, "## Progress", "", f"**{derive(rows, prs)}**", "",
             "Pull requests: " + (", ".join(f"#{n} ({s})" for n, s in sorted(prs.items())) or "none yet") + f" · updated {today}", "",
             "| | Criterion | State | Evidence |", "|---|---|---|---|"]
    for r in rows:
        box = "x" if r["state"] in DONE else " "
        lines.append(f"| [{box}] | {clean(r['criterion'])} | {r['state']} | {clean(r.get('evidence', ''))} |")
    lines += ["", END]
    return "\n".join(lines)


def parse(body: str) -> tuple[list[dict], dict[int, str]]:
    """The rows and pull requests of the block in ``body`` (none, if it has no block)."""
    if START not in body:
        return [], {}
    block = body.split(START, 1)[1].split(END, 1)[0]
    rows, prs = [], {}
    for line in block.splitlines():
        m = re.match(r"\|\s*\[( |x)\]\s*\|\s*(.*?)\s*\|\s*([a-z ]+?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if m and m.group(3) in STATES:
            rows.append({"criterion": m.group(2), "state": m.group(3), "evidence": m.group(4)})
        if line.startswith("Pull requests:"):
            prs = {int(n): s for n, s in re.findall(r"#(\d+) \((\w+)\)", line)}
    return rows, prs


def put(body: str, block: str) -> str:
    if START in body:
        head, rest = body.split(START, 1)
        tail = rest.split(END, 1)[1] if END in rest else ""
        return head.rstrip("\n") + "\n\n" + block + tail
    return block + "\n\n" + body.lstrip("\n")


def checklist_rows(body: str) -> list[dict]:
    """The acceptance criteria already written as ``- [ ]`` lines in an issue body, as open rows."""
    out = []
    for m in re.finditer(r"^\s*- \[( |x)\] (.+)$", body.split(START, 1)[0] if START in body else body, re.M):
        out.append({"criterion": m.group(2).strip(), "state": "verified" if m.group(1) == "x" else "open", "evidence": ""})
    return out


def refs_in(text: str) -> list[int]:
    found = []
    for m in REFS.finditer(text or ""):
        found += [int(n) for n in re.findall(r"#(\d+)", m.group(1))]
    return sorted(set(found))


# -- GitHub -------------------------------------------------------------------------------------------------------------

def gh(*args: str) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        sys.exit(f"gh {' '.join(args[:3])} failed: {done.stderr.strip()}")
    return done.stdout


def read_issue(n: int) -> dict:
    return json.loads(gh("issue", "view", str(n), "--json", "body,state,labels,title"))


def write_body(n: int, body: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8", newline="\n") as f:
        f.write(body)
    gh("issue", "edit", str(n), "--body-file", f.name)


def update(n: int, mutate) -> str:
    issue = read_issue(n)
    body = issue["body"] or ""
    rows, prs = parse(body)
    if not rows:
        rows = checklist_rows(body)
    rows, prs = mutate(rows, prs) or (rows, prs)
    new = put(body, render(rows, prs))
    if new != body:
        write_body(n, new)
    return derive(rows, prs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show"); p.add_argument("issue", type=int)
    p = sub.add_parser("tick"); p.add_argument("issue", type=int); p.add_argument("criterion"); p.add_argument("--evidence", required=True)
    p.add_argument("--state", default="verified", choices=STATES)
    p = sub.add_parser("state"); p.add_argument("issue", type=int); p.add_argument("state", choices=("verified",))
    p = sub.add_parser("set"); p.add_argument("issue", type=int); p.add_argument("--file", required=True)
    p.add_argument("--pr", action="append", default=[], help="N:state (open|merged); repeatable")
    p = sub.add_parser("pr-merged"); p.add_argument("--pr", type=int, required=True)
    p = sub.add_parser("pr-opened"); p.add_argument("--pr", type=int, required=True)
    p = sub.add_parser("close"); p.add_argument("issue", type=int)
    args = ap.parse_args(argv)

    if args.cmd == "show":
        body = read_issue(args.issue)["body"] or ""
        rows, prs = parse(body)
        print(derive(rows or checklist_rows(body), prs))
        return 0
    if args.cmd in ("pr-merged", "pr-opened"):
        pr = json.loads(gh("pr", "view", str(args.pr), "--json", "body,state,mergedAt,mergeCommit,title"))
        target = "merged" if args.cmd == "pr-merged" else "open"
        issues = refs_in(pr["body"])
        if not issues:
            print(f"PR #{args.pr} names no issue (`Refs #n`): nothing to update")
            return 0
        for n in issues:
            def mutate(rows, prs, n=n):
                prs[args.pr] = target
                for r in rows:  # criteria this PR claims are made true stay 'open' until someone ticks them with evidence
                    if target == "open" and r["state"] == "open":
                        r["state"] = "in review"
                    if target == "merged" and r["state"] == "in review":
                        r["state"] = "merged"
                return rows, prs
            line = update(n, mutate)
            sha = (pr.get("mergeCommit") or {}).get("oid", "")[:7]
            closes = n in {int(x) for m in CLOSES.finditer(pr.get("body") or "") for x in re.findall(r"#(\d+)", m.group(1))}
            if target == "merged" and closes:
                note = (f"PR #{args.pr} merged ({sha}) and, because it said Closes, GitHub closes this issue. Rows still marked open or merged "
                        "in the Progress block above are checks to run after deploy; if one fails, this issue is reopened.")
            else:
                note = (f"PR #{args.pr} merged ({sha}). Merged is not verified: the Progress block above says what is left."
                        if target == "merged" else f"PR #{args.pr} opened for this issue. Progress: {line}")
            gh("issue", "comment", str(n), "--body", f"{note}\n\n**{line}**")
        return 0
    if args.cmd == "tick":
        def mutate(rows, prs):
            hits = [r for r in rows if args.criterion.lower() in r["criterion"].lower()]
            if len(hits) != 1:
                sys.exit(f"{len(hits)} criteria match {args.criterion!r}: be more specific" if hits else "no criterion matches")
            hits[0]["state"], hits[0]["evidence"] = args.state, args.evidence
            return rows, prs
        print(update(args.issue, mutate))
        return 0
    if args.cmd == "set":
        data = json.load(open(args.file, encoding="utf-8"))

        def mutate(rows, prs):
            for r in data:
                if r["state"] not in STATES:
                    sys.exit(f"unknown state {r['state']!r}")
            for item in args.pr:
                number, state = item.split(":")
                prs[int(number)] = state
            return [{"criterion": r["criterion"], "state": r["state"], "evidence": r.get("evidence", "")} for r in data], prs
        print(update(args.issue, mutate))
        return 0
    if args.cmd in ("state", "close"):
        issue = read_issue(args.issue)
        rows, prs = parse(issue["body"] or "")
        left = [r for r in rows if r["state"] not in DONE]
        if left or not rows:
            sys.exit(f"#{args.issue} cannot be {'verified' if args.cmd == 'state' else 'closed'}: " + (f"{len(left)} criteria are not verified or waived" if rows else "it has no Progress block"))
        if args.cmd == "close":
            gh("issue", "close", str(args.issue), "--reason", "completed")
        print("closed" if args.cmd == "close" else "all verified")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

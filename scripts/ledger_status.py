"""Where every issue of the verification ledger stands today, read live from GitHub (issue #242, AGENTS.md section 9).

The ledger (``docs/verification-ledger.md``) is the audit of 2026-10-07: a historical record that is never rewritten. This script
adds to it. For each issue the ledger lists (the first column of its table) plus the seven that were reopened before it, it reads the
issue with ``gh`` (state, closing date, labels, the Progress block that ``scripts/issue_progress.py`` keeps) and writes a Markdown
section "State on <date>": one row per issue, the verdict, the open rows with what blocks each, the totals, the issues that are not
verified and why, and the verified rows whose evidence names no test, pull request, document or dated run (it reports, it never
invents evidence).

    python scripts/ledger_status.py                      # print the section
    python scripts/ledger_status.py --output section.md  # write it to a file, to append to the ledger

Verdicts: **Verified** (the issue is closed and every row is verified or waived), **Partly verified** (some rows are done, others
are open, or every row is done but the issue is still open), **Not verified** (no rows, or none of them verified).
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import issue_progress as ip  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "verification-ledger.md"
REPO_URL = "https://github.com/colombod-personal/the-vault"
EXTRA = (83, 205, 216, 217, 220, 221, 227)  # reopened before the audit's own list was written
VERIFIED, PARTLY, NOT = "Verified", "Partly verified", "Not verified"

TEST_REF = re.compile(r"tests/[\w./-]+\.py(?:::\w+)?")
PR_REF = re.compile(r"(?i)\bPR\s*#?\d+|/pull/\d+")
DOC_REF = re.compile(r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.(?:md|py|yml|yaml|json|js|jsx|ts|tsx|css|html|txt|toml|sh)\b|\b(?:README|AGENTS|CLAUDE)\.md")
LINK_REF = re.compile(r"/actions/runs/\d+|#issuecomment-\d+")
RUN_REF = re.compile(r"\d{4}-\d{2}-\d{2}")
RUN_WORDS = re.compile(r"(?i)production|prod\b|real run|real app|ran |run against|run for|concluded|workflow|chatgpt|claude|mtgvault\.cards|verify_production|\bCI\b")


# -- Reading the ledger ------------------------------------------------------------------------------------------------

def ledger_issue_numbers(text: str) -> list[int]:
    """The issue numbers in the first column of the audit's table (it stops at the first per-issue detail heading), plus the extras."""
    table = text.split("## Criterion by criterion", 1)[0]
    found = [int(n) for n in re.findall(r"(?m)^\|\s*#(\d+)\s*\|", table)]
    return sorted(set(found) | set(EXTRA))


# -- Pure logic (the test calls these with fixture data) ---------------------------------------------------------------

def counts(rows: list[dict]) -> dict[str, int]:
    verified = sum(r["state"] == "verified" for r in rows)
    waived = sum(r["state"] == "waived" for r in rows)
    return {"verified": verified, "waived": waived, "open": len(rows) - verified - waived, "total": len(rows)}


def verdict(rows: list[dict], state: str) -> str:
    c = counts(rows)
    if not rows or c["verified"] + c["waived"] == 0:
        return NOT
    if c["open"] == 0 and state.upper() == "CLOSED":
        return VERIFIED
    return PARTLY


BOILERPLATE = re.compile(r"(?i)needs a real (?:claude\.ai )?run(?: \([^)]*\))?:?")
HOST = re.compile(r"(?i)claude|chatgpt|codex|cursor|copilot|real app|real host|\bhosts?\b|connector|harness|browser|screen reader|voiceover|nvda|phone")
PRODUCTION = re.compile(r"(?i)production|\bprod\b|deploy|mtgvault\.cards|vercel|neon|nightly|\blogs?\b")
OWNER = re.compile(r"(?i)\bowner\b|\bdecision\b|\bdecide\b")


def blocker(row: dict, labels: set[str]) -> str:
    """What an unfinished row waits for, from the row's own state and text, then the issue's labels."""
    criterion = row["criterion"]
    text = f"{criterion} {BOILERPLATE.sub(' ', row.get('evidence', ''))}"
    if row["state"] == "owner" or OWNER.search(text):
        why = "the owner"
    elif row["state"] == "in review":
        why = "a pull request in review"
    elif HOST.search(criterion):
        why = "a run in the real app (Claude, ChatGPT, a harness or a browser)"
    elif PRODUCTION.search(text):
        why = "a run on production"
    elif HOST.search(text):
        why = "a run in the real app (Claude, ChatGPT, a harness or a browser)"
    elif row["state"] == "merged":
        why = "a check after deploy (merged, not seen working)"
    else:
        why = "work or evidence not yet done"
    notes = [f"label {label}" for label in ("waiting-owner", "status:blocked") if label in labels]
    return why + (f" ({', '.join(notes)})" if notes else "")


def evidence_kinds(evidence: str) -> list[str]:
    """Which kinds of reference the evidence text of a row names: a test, a pull request, a document, a linked run or comment, a dated run."""
    kinds = []
    if TEST_REF.search(evidence):
        kinds.append("test")
    if PR_REF.search(evidence):
        kinds.append("pr")
    if DOC_REF.search(evidence):
        kinds.append("doc")
    if LINK_REF.search(evidence):
        kinds.append("link")
    if RUN_REF.search(evidence) and RUN_WORDS.search(evidence):
        kinds.append("run")
    return kinds


def summarize(issue: dict) -> dict:
    """One issue's standing from what ``gh issue view --json number,title,state,closedAt,labels,body,url`` returned."""
    rows, _prs = ip.parse(issue.get("body") or "")
    labels = {(label["name"] if isinstance(label, dict) else label) for label in issue.get("labels", [])}
    state = issue["state"].upper()
    unfinished = [r for r in rows if r["state"] not in ip.DONE]
    verified_rows = [r for r in rows if r["state"] == "verified"]
    return {
        "number": issue["number"], "title": issue["title"], "state": state,
        "closed": (issue.get("closedAt") or "")[:10] if state == "CLOSED" else "",
        "url": issue.get("url") or f"{REPO_URL}/issues/{issue['number']}",
        "labels": sorted(labels), "counts": counts(rows), "verdict": verdict(rows, state),
        "open_rows": [{"criterion": r["criterion"], "state": r["state"], "why": blocker(r, labels)} for r in unfinished],
        "no_reference": [{"criterion": r["criterion"], "evidence": r.get("evidence", "")}
                         for r in verified_rows if not evidence_kinds(r.get("evidence", ""))],
        "verified_rows": len(verified_rows),
    }


def short(text: str, n: int) -> str:
    text = ip.clean(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "..."


def reason_not_verified(s: dict) -> str:
    c = s["counts"]
    if not c["total"]:
        return "no Progress block or no acceptance criteria are recorded in the issue, so nothing can be verified"
    waits = sorted({r["why"].split(" (")[0] for r in s["open_rows"]})
    return f"none of its {c['total']} rows is verified yet; waiting for " + "; ".join(waits)


def render_section(issues: list[dict], today: str) -> str:
    """The Markdown section to append to the ledger, from the issues as ``gh`` returned them."""
    ss = sorted((summarize(i) for i in issues), key=lambda s: s["number"])
    by = {v: [s for s in ss if s["verdict"] == v] for v in (VERIFIED, PARTLY, NOT)}
    rows_total = sum(s["counts"]["total"] for s in ss)
    rows_done = sum(s["counts"]["verified"] + s["counts"]["waived"] for s in ss)
    gaps = [(s, g) for s in ss for g in s["no_reference"]]
    out = [f"## State on {today}", "",
           f"Generated by `scripts/ledger_status.py` on {today} from a live read of each issue with `gh` (state, labels and the Progress block that "
           "`scripts/issue_progress.py` keeps). The audit above is the record of 2026-10-07 and is unchanged; this section is the board today. "
           "An issue is **Verified** when it is closed and every row of its Progress block is verified or waived; **Partly verified** when some rows "
           "are done and others are not (or every row is done but the issue is still open); **Not verified** when it has no rows or none of them is verified.",
           "",
           f"**{len(ss)} issues: {len(by[VERIFIED])} verified, {len(by[PARTLY])} partly verified, {len(by[NOT])} not verified.** "
           f"Rows: {rows_done} of {rows_total} verified or waived, {rows_total - rows_done} open. "
           f"Issues still open on GitHub: {sum(s['state'] == 'OPEN' for s in ss)}; closed: {sum(s['state'] == 'CLOSED' for s in ss)}. "
           f"Verified rows whose evidence names no test, pull request, document or dated run: {len(gaps)} (listed below).",
           "",
           "| Issue | Title | GitHub | Closed | Verified | Waived | Open | Rows | Verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for s in ss:
        c = s["counts"]
        out.append(f"| [#{s['number']}]({s['url']}) | {short(s['title'], 70)} | {s['state'].lower()} | {s['closed'] or '-'} | "
                   f"{c['verified']} | {c['waived']} | {c['open']} | {c['total']} | {s['verdict']} |")

    out += ["", f"### Not verified ({len(by[NOT])})", ""]
    out += [f"- [#{s['number']}]({s['url']}) {short(s['title'], 80)}: {reason_not_verified(s)}." for s in by[NOT]] or ["None."]

    out += ["", f"### Partly verified: the open rows and what each waits for ({len(by[PARTLY])} issues)", ""]
    for s in by[PARTLY]:
        c = s["counts"]
        out.append(f"**[#{s['number']}]({s['url']}) {short(s['title'], 80)}** ({s['state'].lower()}; {c['verified'] + c['waived']} of {c['total']} rows done)")
        if not s["open_rows"]:
            out.append("- every row is verified or waived but the issue is still open: it can be closed with `python scripts/issue_progress.py close`")
        out += [f"- {short(r['criterion'], 170)} [{r['state']}]: waits for {r['why']}" for r in s["open_rows"]]
        out.append("")
    if not by[PARTLY]:
        out += ["None.", ""]

    out += [f"### Verified rows whose evidence names no test, pull request, document or dated run ({len(gaps)})", "",
            "Only reported: the evidence text is in the issue and is not changed here. A row is listed when its evidence contains no `tests/...py::name`, "
            "no pull request number, no path to a document or source file, no link to a workflow run or an issue comment, and no dated production or real-app run.",
            ""]
    for s, g in gaps:
        out.append(f"- [#{s['number']}]({s['url']}): {short(g['criterion'], 120)} (evidence: {short(g['evidence'], 100) or 'empty'})")
    if not gaps:
        out.append("None.")
    return "\n".join(out) + "\n"


# -- GitHub ------------------------------------------------------------------------------------------------------------

def read_issues(numbers: list[int]) -> list[dict]:
    wanted = set(numbers)
    listed = json.loads(ip.gh("issue", "list", "--state", "all", "--limit", "1000",
                              "--json", "number,title,state,closedAt,labels,body,url"))
    found = {i["number"]: i for i in listed if i["number"] in wanted}
    for n in sorted(wanted - set(found)):  # anything the list missed is read one by one
        found[n] = json.loads(ip.gh("issue", "view", str(n), "--json", "number,title,state,closedAt,labels,body,url"))
    return [found[n] for n in sorted(found)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--output", help="write the section to this file instead of printing it")
    args = ap.parse_args(argv)
    numbers = ledger_issue_numbers(Path(args.ledger).read_text(encoding="utf-8"))
    text = render_section(read_issues(numbers), args.date)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

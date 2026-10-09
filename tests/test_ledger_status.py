"""The ledger's "State on <date>" section (issue #242): verdicts, what blocks an open row, the evidence gaps and the rendering, from
fixture issues (no network)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import issue_progress as ip  # noqa: E402
import ledger_status as ls  # noqa: E402


def row(criterion, state, evidence=""):
    return {"criterion": criterion, "state": state, "evidence": evidence}


def issue(number, title, state, rows, labels=(), closed=None):
    body = "The ask.\n\n" + ip.render(rows, {}, today="2026-10-08") if rows else "The ask, no progress block."
    return {"number": number, "title": title, "state": state, "closedAt": closed, "body": body,
            "labels": [{"name": n} for n in labels], "url": f"https://example.test/issues/{number}"}


GOOD = [row("Rate limits", "verified", "tests/test_rate_limits.py::test_limited"), row("Dropped", "waived", "owner said so, 2026-10-08")]
FIXTURE = [
    issue(3, "All rows done, closed", "CLOSED", GOOD, closed="2026-10-08T12:00:00Z"),
    issue(14, "All rows done but still open", "OPEN", GOOD),
    issue(29, "Some rows open", "OPEN", [row("Parses", "verified", "PR #265"), row("Owner step", "owner", "owner action: send the draft"),
                                          row("Works in ChatGPT", "open", "needs a real run (browser, Claude, ChatGPT or production): not recorded"),
                                          row("Latency on Vercel", "merged", "PR #272: timings on production not read")],
          labels=("waiting-owner", "status:blocked")),
    issue(50, "Nothing verified", "OPEN", [row("a", "open"), row("b", "merged", "PR #1")]),
    issue(83, "No progress block", "OPEN", []),
    issue(90, "Verified without a reference", "CLOSED",
          [row("Fetched again", "verified", "fixed earlier on 2026-10-07: see the comments of this issue"),
           row("Deck tab", "verified", "public/views/deck.jsx DeckLibrary"),
           row("Run", "verified", "production run 2026-10-08: 18 passed")], closed="2026-10-09T01:00:00Z"),
]


def test_the_ledger_table_gives_the_issue_numbers_and_the_seven_reopened_ones_are_added():
    text = "| Issue | Title |\n|---|---|\n| #3 | A |\n| #14 | B |\n\n## Criterion by criterion\n\n| #99 | not in the first table |\n"
    assert ls.ledger_issue_numbers(text) == sorted({3, 14, 83, 205, 216, 217, 220, 221, 227})
    real = ls.ledger_issue_numbers(ls.LEDGER.read_text(encoding="utf-8"))
    assert len(real) == 85 and set(ls.EXTRA) <= set(real) and 3 in real and 210 in real


def test_a_verdict_needs_every_row_done_and_the_issue_closed():
    assert ls.verdict(GOOD, "CLOSED") == ls.VERIFIED
    assert ls.verdict(GOOD, "OPEN") == ls.PARTLY  # done but not closed: not verified yet
    assert ls.verdict([*GOOD, row("x", "open")], "CLOSED") == ls.PARTLY
    assert ls.verdict([row("x", "merged"), row("y", "open")], "OPEN") == ls.NOT
    assert ls.verdict([], "OPEN") == ls.NOT and ls.verdict([], "CLOSED") == ls.NOT
    assert ls.counts([*GOOD, row("x", "owner")]) == {"verified": 1, "waived": 1, "open": 1, "total": 3}


def test_an_open_row_says_what_it_waits_for():
    assert ls.blocker(row("Send the draft", "owner"), set()) == "the owner"
    assert ls.blocker(row("Works in ChatGPT", "open", "needs a real run (browser, Claude, ChatGPT or production): x"), set()).startswith("a run in the real app")
    assert ls.blocker(row("Latency and cost", "open", "time the first call on a cold production function"), set()) == "a run on production"
    assert ls.blocker(row("Seen working", "merged"), set()).startswith("a check after deploy")
    assert ls.blocker(row("Plain", "open"), {"waiting-owner", "status:blocked"}) == "work or evidence not yet done (label waiting-owner, label status:blocked)"
    assert ls.blocker(row("Review", "in review"), set()) == "a pull request in review"


def test_evidence_is_checked_for_a_test_a_pull_request_a_document_or_a_dated_run():
    assert {"test", "pr"} <= set(ls.evidence_kinds("tests/test_x.py::test_y, also PR #12"))
    assert ls.evidence_kinds("see docs/twins.md and .github/workflows/tests.yml") == ["doc"]
    assert ls.evidence_kinds("public/views/deck.jsx DeckLibrary") == ["doc"]
    assert ls.evidence_kinds("run against production 2026-10-07 18:19: 18 passed") == ["run"]
    assert "link" in ls.evidence_kinds("https://github.com/o/r/issues/5#issuecomment-123")
    assert ls.evidence_kinds("fixed earlier on 2026-10-07: see the comments of this issue") == []  # a date alone is not a run
    assert ls.evidence_kinds("") == []


def test_one_issue_is_summarised_from_what_github_returns():
    s = ls.summarize(FIXTURE[2])
    assert s["verdict"] == ls.PARTLY and s["counts"] == {"verified": 1, "waived": 0, "open": 3, "total": 4} and s["state"] == "OPEN"
    whys = [r["why"] for r in s["open_rows"]]
    assert whys[0].startswith("the owner") and whys[1].startswith("a run in the real app") and whys[2].startswith("a run on production")
    assert all("label waiting-owner, label status:blocked" in w for w in whys)
    assert s["closed"] == "" and ls.summarize(FIXTURE[0])["closed"] == "2026-10-08"
    gaps = ls.summarize(FIXTURE[5])["no_reference"]
    assert [g["criterion"] for g in gaps] == ["Fetched again"]  # the others name a document and a dated run


def test_the_section_states_the_totals_the_unverified_issues_and_the_gaps():
    text = ls.render_section(FIXTURE, "2026-10-09")
    assert text.startswith("## State on 2026-10-09")
    assert "**6 issues: 2 verified, 2 partly verified, 2 not verified.**" in text
    assert "Issues still open on GitHub: 4; closed: 2." in text
    assert "| [#3](https://example.test/issues/3) | All rows done, closed | closed | 2026-10-08 | 1 | 1 | 0 | 2 | Verified |" in text
    assert "| [#29](https://example.test/issues/29) | Some rows open | open | - | 1 | 0 | 3 | 4 | Partly verified |" in text
    not_verified = text.split("### Not verified (2)", 1)[1].split("###", 1)[0]
    assert "[#83]" in not_verified and "no Progress block" in not_verified and "[#50]" in not_verified and "none of its 2 rows is verified" in not_verified
    partly = text.split("### Partly verified", 1)[1].split("### Verified rows", 1)[0]
    assert "Owner step [owner]: waits for the owner (label waiting-owner, label status:blocked)" in partly
    assert "every row is verified or waived but the issue is still open" in partly  # #14
    assert "(1)" in text.split("### Verified rows", 1)[1].splitlines()[0]
    assert "[#90](https://example.test/issues/90): Fetched again" in text and "Deck tab (evidence" not in text
    assert text.count("\n|") == 2 + len(FIXTURE)  # only the summary table, one line per issue
    assert "|" not in ls.short("a | b", 20)  # a pipe in a title cannot break the table

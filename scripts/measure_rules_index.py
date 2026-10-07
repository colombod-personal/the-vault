"""Measure the ways of checking a rules citation and of noticing a new edition, on Wizards' real current file (#144).

    python scripts/measure_rules_index.py                      # approach B only (no database needed)
    DATABASE_URL=postgresql://... python scripts/measure_rules_index.py   # also approach A (a temporary table, gone at the end)

Nothing of the rules is kept: the file is read into memory (as the Vault does), approach A's stemmed index lives in a TEMP table
that disappears with the connection, and the output is numbers. The results are recorded in ``docs/rules-index.md``; run this again
when the design changes. The sample is seeded, so a run on the same edition gives the same quotes.

Citation checking: for 300 rules, a true quote (8 to 16 consecutive words of the rule) and the same quote altered in a way that
changes its meaning or its wording (a negation added, a word swapped for another, a plural toggled, a small word dropped, two words
swapped) and the true quote attributed to the wrong rule. A good check accepts every true quote and rejects every altered one.
"""

from __future__ import annotations

import json
import os
import random
import re
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault import catalog_queries as q  # noqa: E402
from vault import rules_live  # noqa: E402

HEADERS = {"User-Agent": rules_live.USER_AGENT, "Accept": "*/*"}
MODALS = ("can", "may", "must", "does", "is", "are", "has", "have", "will", "would", "could", "should")
SMALL = ("the", "a", "an", "of", "to", "that", "and")
N_RULES = 300


def ms(fn, repeat=1):
    out, took = None, []
    for _ in range(repeat):
        t = time.perf_counter()
        out = fn()
        took.append((time.perf_counter() - t) * 1000)
    return out, took


def summary(took):
    return {"median_ms": round(statistics.median(took), 1), "max_ms": round(max(took), 1), "runs": len(took)}


def sample_quotes(edition, rng):
    rows = [r for r in edition.rows.values() if r["kind"] == "rule" and len(r["text"].split("\n")[0].split()) >= 18]
    rng.shuffle(rows)
    vocabulary = [w for r in rows[:400] for w in r["text"].split() if len(w) >= 5 and w.isalpha()]
    cases = []
    for rule in rows[:N_RULES]:
        words = rule["text"].split("\n")[0].split()
        length = rng.randint(8, 16)
        start = rng.randint(0, len(words) - length)
        true = words[start:start + length]
        case = {"rule": rule["number"], "true": " ".join(true), "altered": {}}
        spots = [i for i, w in enumerate(true) if w.lower().strip(",.") in MODALS]
        if spots:
            i = spots[0]
            case["altered"]["negation"] = " ".join(true[:i + 1] + ["not"] + true[i + 1:])
        inner = [i for i, w in enumerate(true) if 0 < i < len(true) - 1]  # an alteration at either end would leave a shorter true quote
        long = [i for i in inner if len(true[i]) >= 5 and true[i].isalpha()]
        if long:
            i = rng.choice(long)
            other = rng.choice([w for w in vocabulary if w != true[i]])
            case["altered"]["word swapped"] = " ".join(true[:i] + [other] + true[i + 1:])
            j = rng.choice(long)
            word = true[j]
            plural = word[:-1] if word.endswith("s") else word + "s"
            case["altered"]["plural toggled"] = " ".join(true[:j] + [plural] + true[j + 1:])
        small = [i for i in inner if true[i].lower() in SMALL]
        if small:
            i = small[0]
            case["altered"]["small word dropped"] = " ".join(true[:i] + true[i + 1:])
        pairs = [i for i in inner[:-1] if true[i] != true[i + 1]]
        if pairs:
            i = rng.choice(pairs)
            case["altered"]["two words swapped"] = " ".join(true[:i] + [true[i + 1], true[i]] + true[i + 2:])
        if len(true[-1]) >= 5 and true[-1].isalpha():
            case["altered"]["last word cut short"] = " ".join(true[:-1] + [true[-1][:-1]])
        text = q._squash(rule["text"])
        case["altered"] = {k: v for k, v in case["altered"].items() if q._squash(v) not in text or k == "last word cut short"}  # still verbatim: not altered
        wrong = rng.choice([r for r in rows[:N_RULES] if r["number"] != rule["number"]])
        case["wrong_rule"] = wrong["number"]
        cases.append(case)
    return cases


def check_b(edition):
    """The Vault's own check (api/catalog_api.py verify): the quote, whitespace and quotes squashed, is inside the rule's text."""
    return lambda number, quote: (lambda r: bool(r) and q._squash(quote) in q._squash(r["text"]))(edition.find(number))


def check_a(edition, url):
    """Approach A: a stemmed full-text index per rule number and no text, as Postgres makes it: the quote must be a phrase in it."""
    from sqlalchemy import create_engine, text

    from vault.db import normalise_url

    engine = create_engine(normalise_url(url))
    conn = engine.connect()
    conn.execute(text("CREATE TEMP TABLE rules_tsv (number text PRIMARY KEY, tsv tsvector)"))
    rows = [(n, r["text"]) for n, r in edition.rows.items() if r["kind"] in ("rule", "heading")]
    for i in range(0, len(rows), 500):
        conn.execute(text("INSERT INTO rules_tsv SELECT n, to_tsvector('english', t) FROM unnest(CAST(:n AS text[]), CAST(:t AS text[])) AS u(n, t)"),
                     {"n": [n for n, _ in rows[i:i + 500]], "t": [t for _, t in rows[i:i + 500]]})
    size = conn.execute(text("SELECT sum(pg_column_size(tsv)) FROM rules_tsv")).scalar()

    def check(number, quote):
        return bool(conn.execute(text("SELECT tsv @@ phraseto_tsquery('english', :q) FROM rules_tsv WHERE number = :n"), {"q": quote, "n": number}).scalar())
    return check, size, conn


def evaluate(check, cases):
    accepted_true, took = 0, []
    false_accept: dict[str, list[int]] = {}
    for c in cases:
        t = time.perf_counter()
        accepted_true += check(c["rule"], c["true"])
        took.append((time.perf_counter() - t) * 1000)
        for kind, quote in c["altered"].items():
            hit = check(c["rule"], quote)
            false_accept.setdefault(kind, []).append(int(hit))
        wrong = check(c["wrong_rule"], c["true"])
        false_accept.setdefault("right words, wrong rule", []).append(int(wrong))
    return {"true_accepted": f"{accepted_true}/{len(cases)}",
            "altered_wrongly_accepted": {k: f"{sum(v)}/{len(v)} ({sum(v) / len(v):.0%})" for k, v in false_accept.items()},
            "ms_per_check": round(statistics.mean(took), 3)}


INVENTED = """Magic: The Gathering Comprehensive Rules

These rules are effective as of March 3, 2027.

1. Game Concepts

100. General

100.1. These rules apply to any game.

100.2. Invented for the measurement.

100.3. Also invented.

Glossary

Credits
"""


def detection():
    """How long until a new edition, or a corrected file, is seen: the real LiveRules against the Wizards twin serving an invented
    file, stepping a clock a minute at a time for 8 hours after the change."""
    from twins.universe import Universe

    out = {}
    for scenario in ("new edition (a new file name)", "file corrected in place (same name)"):
        universe = Universe(seed=False)
        universe.wizards.publish(INVENTED, "20270303")
        clock = [0.0]
        live = rules_live.LiveRules(transport=universe.transport, clock=lambda: clock[0])
        live.edition()
        if scenario.startswith("new"):
            universe.wizards.publish(INVENTED.replace("March 3, 2027", "June 9, 2027"), "20270609")
        else:
            universe.wizards.publish(INVENTED.replace("100.3. Also invented.", "100.3. Corrected."), "20270303")
        seen = None
        for minute in range(0, 8 * 60 + 1):
            clock[0] = minute * 60.0
            ed = live.edition()
            if (ed.version == "2027-06-09") if scenario.startswith("new") else ("Corrected" in ed.rows["100.3"]["text"]):
                seen = minute
                break
        out[scenario] = f"seen after {seen} minutes" if seen is not None else "not seen within 8 hours"
    return out


def main() -> None:
    rng = random.Random(20261007)
    report: dict = {}
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        page, took = ms(lambda: client.get(rules_live.RULES_PAGE), 5)
        report["page_get"] = summary(took) | {"bytes": len(page.content)}
        url = rules_live.TXT_LINK.findall(page.text)[0].replace(" ", "%20")
        report["edition_file"] = url.rsplit("/", 1)[-1]
        head, took = ms(lambda: client.head(url), 5)
        report["txt_head"] = summary(took) | {"etag": head.headers.get("etag"), "last_modified": head.headers.get("last-modified")}
        txt, took = ms(lambda: client.get(url), 3)
        report["txt_get"] = summary(took) | {"bytes": len(txt.content)}
    text = txt.content.decode("utf-8-sig")
    tracemalloc.start()
    edition, took = ms(lambda: rules_live.Edition.build(text, url), 3)
    report["parse_and_index"] = summary(took)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report["edition"] = {"version": edition.version, "rules": sum(r["kind"] == "rule" for r in edition.rows.values()),
                         "glossary": sum(r["kind"] == "glossary" for r in edition.rows.values()),
                         "memory_mb_after_one_build": round(current / 1048576, 1)}
    _, took = ms(lambda: edition.search("does trample damage go through when the blocker dies"), 20)
    report["search_query"] = summary(took)
    cases = sample_quotes(edition, rng)
    report["citation"] = {"B: fetch, keep in memory, the Vault's check": evaluate(check_b(edition), cases)}
    db = os.environ.get("DATABASE_URL") or os.environ.get("VAULT_TEST_DATABASE_URL")
    if db:
        check, size, conn = check_a(edition, db)
        report["citation"]["A: stemmed index in Postgres, no text"] = evaluate(check, cases) | {"index_bytes": int(size)}
        conn.close()
    report["detection_simulated"] = detection()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

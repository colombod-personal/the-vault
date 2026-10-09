"""Screenshots and the phone check of the functional-equivalents panel of the Ideas view (#166, docs/functional-equivalents.md section 12).

Drives headless Chromium (CDP, the ``Browser`` of ``scripts/ideas_evidence.py``) against a running local Vault that has a collection and
saved decks (a dev server on a fresh database with synthetic data, signed in through ``/api/auth/dev-login``; never production)::

    python scripts/equivalents_evidence.py --base http://localhost:8166 --out docs/screenshots \\
        --found-deck "Sliver Swarm" --found-card "Doubling Season" --nothing-deck "The Dragon Hoard" --nothing-card "Rhystic Study" \\
        --borrowed-deck "Avatar Aang" --borrowed-card "Rhystic Study" --norole-card "Cloudstone Curio"

Per width (1400 and 390 px) it saves ``ideas-equivalents-<state>-<width>.jpg`` for: ``found`` (same job, the similar tier collapsed), ``texts``
(both Oracle texts open), ``similar`` (the similar tier open), ``nothing`` (nothing does the same job: what the filters set aside, the similar
ones one tap away), ``borrowed`` (found, but another deck holds the copy) and ``norole`` (the Vault knows no role for the card); at 390 px it runs
``scripts/measure_phone.js`` in each (no horizontal overflow, no tap target under 44 px, no text under 12 px). The exit status is non-zero when a
phone check fails. Set ``CHROME`` to a chrome-headless-shell or chrome executable.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ideas_evidence import ROOT, Browser  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", default="http://localhost:8166")
    p.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    p.add_argument("--found-deck", required=True, help="a saved deck missing a card that an owned card does the same job as, with a similar one beside it")
    p.add_argument("--found-card", required=True)
    p.add_argument("--nothing-deck", required=True, help="a deck missing a card whose same-job cards are all outside its colours")
    p.add_argument("--nothing-card", required=True)
    p.add_argument("--borrowed-deck", required=True, help="a deck missing a card whose only same-job candidate is held by another deck")
    p.add_argument("--borrowed-card", required=True)
    p.add_argument("--norole-card", required=True, help="a missing card of --found-deck that the rules read no role in")
    p.add_argument("--widths", default="1400,390")
    p.add_argument("--port", type=int, default=9366)
    args = p.parse_args()
    chrome = os.environ.get("CHROME") or shutil.which("chrome-headless-shell") or shutil.which("chromium") or shutil.which("google-chrome")
    if not chrome:
        print("set CHROME to a Chromium executable", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    measure = (ROOT / "scripts" / "measure_phone.js").read_text(encoding="utf-8")
    measure = measure[measure.index("(() =>"):].rstrip().rstrip(";")
    states = [  # name, deck, card, selector that says the page is ready, an action once it is
        ("found", args.found_deck, args.found_card, ".ideas-alt", None),
        ("texts", args.found_deck, args.found_card, ".ideas-alt", "document.querySelector('.ideas-texts summary').click()"),
        ("similar", args.found_deck, args.found_card, ".ideas-alt", "document.querySelector('.ideas-similar button').click()"),
        ("nothing", args.nothing_deck, args.nothing_card, ".ideas-none", None),
        ("borrowed", args.borrowed_deck, args.borrowed_card, ".ideas-alt", None),
        ("norole", args.found_deck, args.norole_card, ".ideas-norole", None),
    ]
    failed = []
    for width in [int(w) for w in args.widths.split(",")]:
        b = Browser(chrome, width, args.port)
        try:
            b.call("Page.navigate", url=args.base + "/")
            time.sleep(1)
            if b.js("fetch('/api/auth/dev-login', { method: 'POST' }).then(r => r.status)") != 200:
                raise SystemExit("the dev login failed")
            ids = dict(b.js("fetch('/api/v1/decks').then(r => r.json()).then(j => j.items.map(d => [d.name, String(d.id)]))"))
            for name, deck, card, ready, action in states:
                b.go(args.base, f"#/ideas/{ids[deck]}/{urllib.parse.quote(card, safe='')}")
                b.wait_for(f"!!document.querySelector({json.dumps(ready)})")
                time.sleep(0.6)
                b.wait_for("[...document.images].every(i => i.complete)")
                if action:
                    b.js(action)
                    time.sleep(0.4)
                if width < 800:
                    r = b.js(measure)
                    bad = r["overflowX"] or r["targets"]["under44"] or r["text"]["small"]
                    print(f"{'FAIL' if bad else 'ok  '} 390 px {name}: overflowX={r['overflowX']}, {r['targets']['count']} targets, under 44 px: "
                          f"{[(u['text'], u['h']) for u in r['targets']['under44']]}, too small: {[(t['text'], t['px']) for t in r['text']['small']]}")
                    if bad:
                        failed.append(f"{name} at 390")
                b.shot(out / f"ideas-equivalents-{name}-{width}.jpg", whole_page=True)
                print("saved", f"ideas-equivalents-{name}-{width}.jpg")
        finally:
            b.close()
    print("\n" + ("all phone checks passed" if not failed else f"failed: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

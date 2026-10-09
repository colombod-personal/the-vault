"""Screenshots, the phone check and the performance budget of the Ideas page (#163, docs/deck-ideas-lab-design.md), in a real browser.

It drives headless Chromium over the DevTools protocol against a running Vault that has a collection and saved decks (a local dev
server on a fresh database with synthetic data, signed in through ``/api/auth/dev-login``; never production)::

    DEV_LOGIN=1 python -m uvicorn --factory vault.app:create_app --port 8163      (with a database, a catalog and three saved decks)
    python scripts/ideas_evidence.py --base http://localhost:8163 --out docs/screenshots --deck "Sliver Swarm" --covered "Avatar Aang" \\
        --missing-card "Cloudstone Curio" --no-alternative-card "Swords to Plowshares"

What it does, per width (1400 and 390 px):

- saves ``ideas-<state>-<width>.jpg`` for the five states of the design: the start (``start``), a deck with missing cards (``deck``), a
  missing card with alternatives (``alternatives``), a missing card with none (``none``) and a fully covered deck (``covered``);
- at 390 px, runs ``scripts/measure_phone.js`` in each state (horizontal overflow, tap targets under 44 px, text under 12 px / 11 px
  for labels);
- checks Clear, Esc and Back (each leaves the start or the previous step, with no stale selection) and that ``#/graph`` goes to ``#/ideas``;
- measures the budget: opening the deck on a throttled phone (4x CPU slowdown, a 4G network profile) until its lanes are drawn (under 2 s,
  no main-thread task over 200 ms), the first ``ideas`` page's size (gzipped, under 60 KB), and the images the first render and a
  selected card request (at most 12, none larger than a thumbnail).

The exit status is non-zero when a check fails. Needs ``websockets`` (it is in requirements-lock.txt) and a Chromium: set ``CHROME`` to
a chrome-headless-shell or chrome executable.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
THUMB_MAX = (146, 204)  # Scryfall's "small" image: the largest a thumbnail may be
BUDGET = {"first_render_ms": 2000, "long_task_ms": 200, "first_page_gzip_bytes": 60_000, "images": 12}


class Browser:
    def __init__(self, chrome: str, width: int, port: int):
        self.width, self.height = width, (900 if width > 800 else 844)
        self.profile = tempfile.mkdtemp(prefix="ideas-evidence-")
        self.proc = subprocess.Popen([chrome, f"--remote-debugging-port={port}", "--window-size=1400,900", "--hide-scrollbars",
                                      f"--user-data-dir={self.profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page = None
        for _ in range(80):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://localhost:{port}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.25)
        if page is None:
            raise SystemExit("could not start the browser")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=100_000_000)
        self.n = 0
        self.events: list[dict] = []
        for method in ("Page.enable", "Runtime.enable", "Network.enable"):
            self.call(method)
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=self.height, deviceScaleFactor=1, mobile=width < 800)

    def call(self, method: str, **params):
        self.n += 1
        mine = self.n
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mine:
                return msg.get("result", msg)
            if "method" in msg:
                self.events.append(msg)

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:500])
        return r.get("result", {}).get("value")

    def wait_for(self, expr: str, seconds: float = 15.0):
        end = time.time() + seconds
        while time.time() < end:
            if self.js(expr):
                return True
            time.sleep(0.1)
        raise SystemExit(f"timed out waiting for: {expr}")

    def go(self, base: str, hash_: str = ""):
        """A fresh load of the app at a hash (same-document hash changes would not reload the app)."""
        self.call("Page.navigate", url=base + "/")
        time.sleep(0.4)
        self.js(f"location.hash = {json.dumps(hash_)}")
        self.call("Page.reload")
        self.wait_for("!!document.querySelector('.ideas, .app main')")
        self.js("document.querySelector('button[aria-label=\"Dismiss the welcome\"]')?.click()")

    def shot(self, path: Path, whole_page: bool = False):
        """The viewport, or (whole_page) the page from the top to its end, at most 2600 px, so a panel's actions are in the picture."""
        params: dict = {"format": "jpeg", "quality": 82}
        if whole_page:
            size = self.call("Page.getLayoutMetrics")["cssContentSize"]
            params.update(captureBeyondViewport=True, clip={"x": 0, "y": 0, "width": size["width"], "height": min(size["height"], 2600), "scale": 1})
        path.write_bytes(base64.b64decode(self.call("Page.captureScreenshot", **params)["data"]))

    def key(self, key: str):
        code = {"Escape": 27}.get(key, 0)
        for kind in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=kind, key=key, code=key, windowsVirtualKeyCode=code)

    def close(self):
        self.proc.terminate()
        shutil.rmtree(self.profile, ignore_errors=True)


class Run:
    def __init__(self, args):
        self.args = args
        self.failures: list[str] = []
        self.measure = (ROOT / "scripts" / "measure_phone.js").read_text(encoding="utf-8")
        self.measure = self.measure[self.measure.index("(() =>"):].rstrip().rstrip(";")

    def check(self, ok: bool, what: str):
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            self.failures.append(what)

    def phone(self, b: Browser, label: str):
        r = b.js(self.measure)
        self.check(not r["overflowX"] and not r["targets"]["under44"] and not r["text"]["small"],
                   f"390 px, {label}: overflowX={r['overflowX']}, {r['targets']['count']} targets, under 44 px: "
                   f"{[(u['text'], u['h']) for u in r['targets']['under44']]}, {r['text']['count']} texts, too small: {[(t['text'], t['px']) for t in r['text']['small']]}")

    def states(self, width: int, chrome: str, port: int):
        a = self.args
        out = Path(a.out)
        b = Browser(chrome, width, port)
        try:
            print(f"\n== {width} px")
            self.login(b)
            card_q = lambda name: "#/ideas/{}/{}".format(self.deck_id(b, a.deck), urllib.request.quote(name, safe=""))
            steps = [
                ("start", "#/ideas", ".ideas-deck"),
                ("deck", "#/ideas/" + self.deck_id(b, a.deck), ".ideas-row"),
                ("alternatives", card_q(a.missing_card), ".ideas-alt"),
                ("none", card_q(a.no_alternative_card), ".ideas-none"),
                ("covered", "#/ideas/" + self.deck_id(b, a.covered), ".ideas-covered"),
            ]
            for state, hash_, ready in steps:
                b.go(a.base, hash_)
                b.wait_for(f"!!document.querySelector({json.dumps(ready)})")
                time.sleep(0.6)
                if state == "alternatives":
                    b.wait_for("[...document.images].every(i => i.complete)")
                if width < 800:
                    self.phone(b, state)
                b.shot(out / f"ideas-{state}-{width}.jpg", whole_page=state in ("alternatives", "none"))
                print("saved", f"ideas-{state}-{width}.jpg")
            if width < 800:  # a lane opened (accordion) and the long lane's rows
                b.go(a.base, "#/ideas/" + self.deck_id(b, a.deck))
                b.wait_for("!!document.querySelector('.ideas-lane-toggle')")
                b.js("document.querySelector('.ideas-lane-toggle').click()")
                time.sleep(0.4)
                self.phone(b, "deck with a lane open")
            self.flows(b, width)
        finally:
            b.close()

    def login(self, b: Browser):
        b.call("Page.navigate", url=self.args.base + "/")
        time.sleep(1.0)
        self.check(b.js("fetch('/api/auth/dev-login', { method: 'POST' }).then(r => r.status)") == 200, "signed in with the dev login")

    _ids: dict = {}

    def deck_id(self, b: Browser, name: str) -> str:
        if not self._ids:
            decks = b.js("fetch('/api/v1/decks').then(r => r.json()).then(j => j.items.map(d => [d.name, String(d.id)]))")
            self._ids.update(dict(decks))
        return self._ids[name]

    def flows(self, b: Browser, width: int):
        a = self.args
        d = self.deck_id(b, a.deck)
        h = lambda: b.js("location.hash")
        # #/graph goes to #/ideas
        b.go(a.base, "#/graph")
        b.wait_for("!!document.querySelector('.ideas-deck')")
        self.check(h() == "#/ideas", f"{width} px: #/graph redirects to #/ideas (now {h()})")
        # start -> deck -> card; Esc steps out twice
        b.js("[...document.querySelectorAll('a.ideas-deck')].find(x => x.textContent.includes(%s)).click()" % json.dumps(a.deck))
        b.wait_for("!!document.querySelector('.ideas-row')")
        open_row = "[...document.querySelectorAll('.ideas-row')].find(x => x.dataset.card === %s)" % json.dumps(a.missing_card)
        if width < 800:
            b.js("document.querySelector('.ideas-lane-toggle').click()")
        b.js(open_row + ".click()")
        b.wait_for("!!document.querySelector('.ideas-panel')")
        self.check(h().startswith(f"#/ideas/{d}/"), f"{width} px: a selection is a history entry ({h()})")
        b.key("Escape")
        b.wait_for("!document.querySelector('.ideas-panel')")
        self.check(h() == f"#/ideas/{d}", f"{width} px: Esc on a card goes back to the deck, no stale selection ({h()})")
        b.key("Escape")
        b.wait_for("!!document.querySelector('.ideas-deck')")
        self.check(h() == "#/ideas", f"{width} px: Esc on the deck goes back to the start ({h()})")
        # the browser's Back undoes one step; the panel's Back button agrees with it
        b.js("history.back()")
        b.wait_for("!!document.querySelector('.ideas-row')")
        self.check(h() == f"#/ideas/{d}", f"{width} px: the browser's Back returns to the deck ({h()})")
        if width < 800:
            b.js("document.querySelector('.ideas-lane-toggle').click()")
        b.js(open_row + ".click()")
        b.wait_for("!!document.querySelector('.ideas-panel')")
        length = b.js("history.length")
        b.js("document.querySelector('.ideas-panel-top button').click()")
        b.wait_for("!document.querySelector('.ideas-panel')")
        self.check(h() == f"#/ideas/{d}" and b.js("history.length") == length, f"{width} px: the panel's Back is one step back, with no extra history entry")
        if width < 800:
            b.js("document.querySelector('.ideas-lane-toggle').click()")
        b.js(open_row + ".click()")
        b.wait_for("!!document.querySelector('.ideas-panel')")
        b.js("[...document.querySelectorAll('.ideas-head-side button')].find(x => x.textContent === 'Clear').click()")
        b.wait_for("!!document.querySelector('.ideas-deck')")
        self.check(h() == "#/ideas" and not b.js("!!document.querySelector('.ideas-panel')"), f"{width} px: Clear returns to the start")
        self.check(not [e for e in b.events if e.get("method") == "Runtime.exceptionThrown"], f"{width} px: no uncaught exception in the page")

    def performance(self, chrome: str, port: int):
        a = self.args
        print("\n== budget (390 px, 4x CPU slowdown, 4G network)")
        b = Browser(chrome, 390, port)
        try:
            self.login(b)
            d = self.deck_id(b, a.deck)
            b.go(a.base, "#/ideas")
            b.wait_for("!!document.querySelector('.ideas-deck')")
            cookie = "; ".join(f"{c['name']}={c['value']}" for c in b.call("Network.getCookies", urls=[a.base]).get("cookies", []))
            body = urllib.request.urlopen(urllib.request.Request(f"{a.base}/api/v1/decks/{d}/ideas", headers={"Cookie": cookie})).read()  # noqa: S310
            gz = len(gzip.compress(body, 6))
            self.check(gz < BUDGET["first_page_gzip_bytes"], f"the first ideas page is {len(body):,} bytes, {gz:,} gzipped (budget {BUDGET['first_page_gzip_bytes']:,})")
            b.call("Emulation.setCPUThrottlingRate", rate=4)
            b.call("Network.emulateNetworkConditions", offline=False, latency=150, downloadThroughput=int(1.6 * 1024 * 1024 / 8), uploadThroughput=int(750 * 1024 / 8))
            b.js("""(() => {
              window.__long = [];
              new PerformanceObserver((l) => l.getEntries().forEach((e) => window.__long.push(Math.round(e.duration)))).observe({ type: 'longtask', buffered: true });
              window.__t0 = performance.now(); window.__drawn = null;
              new MutationObserver(() => { if (window.__drawn === null && document.querySelector('.ideas-row')) window.__drawn = performance.now(); })
                .observe(document.body, { childList: true, subtree: true });
              0; })()""")
            b.events.clear()
            b.js("[...document.querySelectorAll('a.ideas-deck')].find(x => x.textContent.includes(%s)).click()" % json.dumps(a.deck))
            b.wait_for("window.__drawn !== null", 30)
            time.sleep(0.5)
            ms = round(b.js("window.__drawn - window.__t0"))
            longest = max(b.js("window.__long") or [0])
            self.check(ms < BUDGET["first_render_ms"], f"opening the deck draws its lanes in {ms} ms on the throttled phone (budget {BUDGET['first_render_ms']} ms)")
            self.check(longest <= BUDGET["long_task_ms"], f"the longest main-thread task is {longest} ms (budget {BUDGET['long_task_ms']} ms)")
            images = [e for e in b.events if e.get("method") == "Network.requestWillBeSent" and e["params"].get("type") == "Image"]
            self.check(len(images) <= BUDGET["images"] and len(images) == 0, f"the first render requests {len(images)} images (the rows carry none; budget {BUDGET['images']})")
            # a card with alternatives: the target and its alternatives' thumbnails only
            b.events.clear()
            if b.js("!!document.querySelector('.ideas-lane-toggle')"):
                b.js("document.querySelector('.ideas-lane-toggle').click()")
            b.js("[...document.querySelectorAll('.ideas-row')].find(x => x.dataset.card === %s).click()" % json.dumps(a.missing_card))
            b.wait_for("!!document.querySelector('.ideas-alt')")
            time.sleep(1.5)
            sizes = b.js("[...document.images].map(i => [i.src, i.naturalWidth, i.naturalHeight, Math.round(i.getBoundingClientRect().width), Math.round(i.getBoundingClientRect().height)])")
            images = [e for e in b.events if e.get("method") == "Network.requestWillBeSent" and e["params"].get("type") == "Image"]
            big = [s for s in sizes if s[1] > THUMB_MAX[0] or s[2] > THUMB_MAX[1] or s[3] > 64 or s[4] > 64]
            self.check(len(images) <= BUDGET["images"], f"a selected card requests {len(images)} images (budget {BUDGET['images']}): {[s[0].rsplit('/', 3)[-3:] for s in sizes]}")
            self.check(not big and all('/small/' in s[0] for s in sizes), f"every image is a thumbnail (natural size at most {THUMB_MAX[0]}x{THUMB_MAX[1]}, drawn at about 40x56): {[(s[1], s[2], s[3], s[4]) for s in sizes]}")
            # information: a cold load of the deck's address, scripts from the CDN included, on the same throttled phone
            b.call("Page.addScriptToEvaluateOnNewDocument", source="""
              window.__cold = null;
              new MutationObserver(() => { if (window.__cold === null && document.querySelector('.ideas-row')) window.__cold = Math.round(performance.now()); })
                .observe(document, { childList: true, subtree: true });""")
            b.call("Network.setCacheDisabled", cacheDisabled=True)
            b.call("Page.navigate", url=f"{a.base}/?cold=1#/ideas/{d}")
            b.wait_for("window.__cold !== null", 60)
            print(f"info: a cold load of #/ideas/{d} (cache off, scripts from the CDN) draws its lanes {b.js('window.__cold')} ms after the navigation starts")
        finally:
            b.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", default="http://localhost:8163")
    p.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    p.add_argument("--deck", required=True, help="a saved deck with missing cards")
    p.add_argument("--covered", required=True, help="a saved deck with every card owned and none borrowed")
    p.add_argument("--missing-card", required=True, help="a missing card of --deck that has owned alternatives")
    p.add_argument("--no-alternative-card", required=True, help="a missing card of --deck with no owned alternative")
    p.add_argument("--widths", default="1400,390")
    p.add_argument("--port", type=int, default=9363)
    args = p.parse_args()
    chrome = os.environ.get("CHROME") or shutil.which("chrome-headless-shell") or shutil.which("chromium") or shutil.which("google-chrome")
    if not chrome:
        print("set CHROME to a Chromium executable", file=sys.stderr)
        return 2
    Path(args.out).mkdir(parents=True, exist_ok=True)
    run = Run(args)
    for width in [int(w) for w in args.widths.split(",")]:
        run.states(width, chrome, args.port)
    run.performance(chrome, args.port)
    print("\n" + ("all checks passed" if not run.failures else f"{len(run.failures)} check(s) failed"))
    return 1 if run.failures else 0


if __name__ == "__main__":
    sys.exit(main())

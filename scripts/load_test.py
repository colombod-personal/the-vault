"""Parallel load test for The Vault's MCP server (#169): several agents calling tools at the same time.

A real expert-council run (five agents in parallel) got 500s and "server isn't responding" from production. This script
makes that shape on demand and says what came back, tool by tool: how many answers were fine, how many were an honest
refusal (429 with a Retry-After, 404), and how many were a server error, a refused or timed-out connection, or a tool
call that ended in an error status of 500 or more. It exits non-zero on any of the last three: *no 5xx under parallel
use* is the criterion.

Against a running Vault (production included; a read-only personal access token is enough, nothing is written)::

    python scripts/load_test.py --url https://mtgvault.cards --token vault_pat_... --clients 5 --calls 20

On this machine, with everything it needs (a Postgres you may fill, the twin universe for Wizards and Archidekt, a real
uvicorn with several workers, a database role with a small connection limit like a small Neon compute's)::

    python scripts/load_test.py --local --database-url postgresql://small_role:pw@localhost:5432/load_db --workers 2

``--local`` creates a catalog, a collection and a deck in that database (it needs the ``pg_trgm`` extension and an empty
or disposable database), starts the twins and the server, runs the plan, and stops them. What it cannot show is in
docs/ai-integration-testing.md (Neon's scale-to-zero, Vercel's instances and timeouts are not on this machine).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vault import rules_live  # noqa: E402

FAILURE_TTL = getattr(rules_live, "FAILURE_TTL", 20)  # seconds a failed read of the rules is remembered (the script also runs against older code)
OK, NOT_FOUND, RATE_LIMITED, BUSY = "ok", "not_found", "429", "503"
BAD = ("5xx", "503", "transport", "rpc_error", "bad_answer")  # outcomes that fail the run (a 503 is an honest "busy", but it is a 5xx)
LOCAL_CARD = "Lightning Bolt"


class Tally:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.outcomes: dict[str, Counter] = defaultdict(Counter)
        self.times: dict[str, list[float]] = defaultdict(list)
        self.notes: list[str] = []
        self.retry_after_missing = 0
        self.busy_ok: frozenset[str] = frozenset()  # tools whose honest "unavailable, retry in N seconds" is expected in this phase

    def add(self, tool: str, outcome: str, seconds: float, note: str = "") -> None:
        with self.lock:
            self.outcomes[tool][outcome] += 1
            self.times[tool].append(seconds)
            if note and outcome in BAD and sum(n.split(": ")[1] == outcome for n in self.notes) < 3:  # a few of each kind
                self.notes.append(f"{tool}: {outcome}: {note}")

    def total(self, outcome: str | None = None) -> int:
        return sum(c.total() if outcome is None else c[outcome] for c in self.outcomes.values())

    def bad(self) -> int:
        expected = sum(self.outcomes[t][BUSY] for t in self.busy_ok)
        return sum(self.total(o) for o in BAD) - expected + self.retry_after_missing  # a 429 or 503 without its hint is a failure too


def classify(res: httpx.Response, tool: str, tally: Tally) -> tuple[str, str]:
    """(outcome, note) of one tools/call answer, from what an agent would see."""
    if res.status_code >= 500 and res.status_code != 503:
        return "5xx", f"HTTP {res.status_code} {res.text[:160]!r}"
    if res.status_code == 429 or res.status_code == 503:
        if not res.headers.get("retry-after"):
            tally.retry_after_missing += 1
        return (RATE_LIMITED if res.status_code == 429 else BUSY), f"HTTP {res.status_code} {res.text[:160]!r}"
    if res.status_code >= 400:
        return "bad_answer", f"HTTP {res.status_code} {res.text[:160]!r}"
    try:
        body = res.json()
    except ValueError:
        return "bad_answer", f"not JSON: {res.text[:160]!r}"
    if "error" in body:
        return "rpc_error", json.dumps(body["error"])[:200]
    result = body.get("result") or {}
    if not result.get("isError"):
        return OK, ""
    content = result.get("structuredContent") or {}
    status = int(content.get("status") or 0)
    if status >= 500 and status != 503:
        return "5xx", f"tool error status {status}: {content.get('detail')!r}"
    if status == 429:
        if not content.get("retry_after_seconds"):
            tally.retry_after_missing += 1
        return RATE_LIMITED, ""
    if status == 503:
        if not content.get("retry_after_seconds"):
            tally.retry_after_missing += 1
        return BUSY, f"tool error status 503: {content.get('detail')!r}"
    if status == 404:
        return NOT_FOUND, ""
    return "bad_answer", f"tool error status {status}: {content.get('detail')!r}"


class Agent:
    """One agent: its own connection, calling tools one after the other."""

    def __init__(self, url: str, token: str, timeout: float):
        self.client = httpx.Client(base_url=url, timeout=timeout, headers={"Authorization": f"Bearer {token}"})
        self.ids = itertools.count(1)

    def call(self, tool: str, arguments: dict, tally: Tally) -> None:
        started = time.perf_counter()
        try:
            res = self.client.post("/api/mcp", json={"jsonrpc": "2.0", "id": next(self.ids), "method": "tools/call",
                                                     "params": {"name": tool, "arguments": arguments}})
            outcome, note = classify(res, tool, tally)
        except httpx.HTTPError as exc:
            outcome, note = "transport", f"{type(exc).__name__}: {exc}"
        tally.add(tool, outcome, time.perf_counter() - started, note)


def plan(card: str, deck_id: int | None, archidekt_id: int | None, mix: str) -> list[tuple[str, dict]]:
    catalog = [("get_card_oracle", {"name": card}), ("get_card_oracle", {"name": card[:-1]}),  # the second: a typo, so suggestions
               ("search_rules", {"query": "replacement effect damage"}), ("get_rule", {"number": "100.1"})]
    own = [("get_collection_summary", {}), ("list_decks", {})]
    if deck_id:
        own += [("get_deck", {"deck_id": deck_id}), ("deck_stats", {"deck_id": deck_id}), ("deck_legality", {"deck_id": deck_id, "format": "commander"})]
    if archidekt_id:
        own.append(("get_archidekt_deck", {"deck_id": archidekt_id}))
    return {"catalog": catalog, "own": own}.get(mix, catalog + own)


def run_load(url: str | list[str], token: str, *, clients: int, calls: int, burst: int, mix: str, card: str, timeout: float,
             deck_id: int | None = None, archidekt_id: int | None = None, busy_ok: frozenset[str] = frozenset()) -> Tally:
    """``clients`` agents each make ``calls`` calls; ``burst`` of an agent's calls are in flight at once (an assistant that
    runs its tool calls in parallel). Everything starts together."""
    urls = [url] if isinstance(url, str) else url  # several instances: each agent is served by one of them, like separate function instances
    tally, steps = Tally(), plan(card, deck_id, archidekt_id, mix)
    tally.busy_ok = busy_ok
    start = threading.Barrier(clients * burst)

    def worker(client_no: int, lane: int) -> None:
        agent = Agent(urls[client_no % len(urls)], token, timeout)
        mine = [steps[(client_no * 3 + lane + i * burst) % len(steps)] for i in range(calls // burst + (1 if lane < calls % burst else 0))]
        start.wait()
        for tool, arguments in mine:
            agent.call(tool, arguments, tally)
        agent.client.close()

    threads = [threading.Thread(target=worker, args=(c, lane)) for c in range(clients) for lane in range(burst)]
    began = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    tally.wall = time.perf_counter() - began  # type: ignore[attr-defined]
    return tally


def report(tally: Tally, title: str) -> None:
    print(f"\n== {title}: {tally.total()} calls in {tally.wall:.1f} s ({tally.total() / tally.wall:.1f}/s)")  # type: ignore[attr-defined]
    print(f"{'tool':26} {'ok':>5} {'404':>4} {'429':>4} {'503':>4} {'5xx':>4} {'net':>4} {'rpc':>4} {'p50 ms':>7} {'p95 ms':>7} {'max ms':>7}")
    for tool in sorted(tally.outcomes):
        o, t = tally.outcomes[tool], sorted(tally.times[tool])
        p95 = t[min(len(t) - 1, int(len(t) * 0.95))]
        print(f"{tool:26} {o[OK]:>5} {o[NOT_FOUND]:>4} {o[RATE_LIMITED]:>4} {o[BUSY]:>4} {o['5xx']:>4} {o['transport']:>4} "
              f"{o['rpc_error'] + o['bad_answer']:>4} {statistics.median(t) * 1000:>7.0f} {p95 * 1000:>7.0f} {t[-1] * 1000:>7.0f}")
    print(f"answers: ok {tally.total(OK)}, not found {tally.total(NOT_FOUND)}, rate limited (429) {tally.total(RATE_LIMITED)}, "
          f"busy (503) {tally.total(BUSY)}; FAILURES {tally.bad()} (503 {tally.total(BUSY)}, other 5xx {tally.total('5xx')}, no answer {tally.total('transport')}, "
          f"JSON-RPC error {tally.total('rpc_error')}, unexpected {tally.total('bad_answer')})")
    if tally.busy_ok:
        print(f"(expected in this phase: the 503 answers of {', '.join(sorted(tally.busy_ok))}, each with a retry hint)")
    if tally.retry_after_missing:
        print(f"!! {tally.retry_after_missing} answer(s) of 429/503 came without a retry hint")
    for note in tally.notes:
        print("  ", note)


# -- the local run --------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, seconds: float = 90) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"nothing answered at {url} within {seconds:.0f} s")


def seed_catalog(database_url: str, cards: int) -> None:
    """Oracle cards (Lightning Bolt and ``cards`` made-up names, so fuzzy matching has a realistic table to search),
    rulings, a tag and a price."""
    from datetime import date

    from sqlalchemy import create_engine, text

    from tests.test_catalog_api import BOLT, card
    from vault import catalog_sync as cs
    from vault.db import Database, normalise_url

    engine = create_engine(normalise_url(database_url))  # a fresh start: the previous run's users, decks and caches would skew this one
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    engine.dispose()
    db = Database(database_url)
    db.migrate()
    syllables = ["bo", "ra", "ki", "mu", "ta", "ne", "lo", "su", "vi", "da", "pe", "go"]
    names = ["".join(syllables[(i // 12 ** p) % 12] for p in range(4)).title() + f" of Place {i}" for i in range(cards)]
    with db.sessions() as s:
        cs.sync_oracle_cards(s, [card(BOLT, LOCAL_CARD, "Lightning Bolt deals 3 damage to any target.")]
                             + [card(f"{i:08d}-0000-0000-0000-000000000000", n, f"{n} deals {i % 5} damage to any target.") for i, n in enumerate(names)])
        cs.sync_rulings(s, [{"object": "ruling", "oracle_id": BOLT, "source": "wotc", "published_at": f"20{10 + i}-01-01",
                             "comment": f"Ruling number {i} about the bolt's target."} for i in range(30)])
        cs.sync_oracle_prices(s, [{"oracle_id": BOLT, "scryfall_id": "p-1111", "usd": 0.69, "usd_foil": 2.5, "eur": 0.5,
                                   "day": date.today(), "source": "scryfall"}])
        for name in ("oracle_cards", "rulings", "oracle_tags", "oracle_prices"):
            cs.record_source(s, name, version=name + "-1", rows=1)
        s.commit()
    db.engine.dispose()


def collection_csv(rows: int) -> bytes:
    lines = ['"sep=,"', "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
             "Price Bought,Date Bought,LOW,MID,MARKET"]
    for i in range(rows):
        lines.append(f"my cards,{1 + i % 3},0,Collected Card {i},C{i % 40:02d},Set {i % 40},{i},Mint,Normal,English,0.10,2024-02-17,0.01,0.20,0.09")
    return ("\n".join(lines) + "\n").encode()


def local_run(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    import uvicorn

    from tests.test_rules_parser import SAMPLE
    from twins.server import create_server
    from twins.universe import Universe

    name = args.database_url.rsplit("/", 1)[-1].split("?")[0]
    if not any(word in name for word in ("load", "test", "scratch")):
        raise SystemExit(f"--local empties its database before it starts, and '{name}' does not look disposable "
                         "(its name must contain load, test or scratch)")
    print(f"emptying and seeding {args.database_url.rsplit('@', 1)[-1]} ...", flush=True)
    seed_catalog(args.database_url, args.cards)

    twin_port = free_port()
    universe = Universe(seed=False)
    universe.wizards.publish(SAMPLE)
    archidekt_id = universe.archidekt.add_deck("Load test deck", "load-tester", [(1, "Sol Ring"), (1, LOCAL_CARD)])["id"]
    twins = uvicorn.Server(uvicorn.Config(create_server(universe, f"http://127.0.0.1:{twin_port}"), host="127.0.0.1",
                                          port=twin_port, log_level="error"))
    threading.Thread(target=twins.run, daemon=True).start()
    wait_for(f"http://127.0.0.1:{twin_port}/_twins/api/state")

    # --workers N starts N separate uvicorn processes on N ports, each with its own connection pool, and each agent talks to
    # one of them: what Vercel does with function instances. (uvicorn's own --workers shares one socket between processes,
    # which on Windows left a few accepted connections unread for as long as the client waited, in 3 of about 20 runs: seen only in
    # that mode, never with separate processes, and not what production does.)
    ports = [free_port() for _ in range(args.workers)]
    bases = [f"http://127.0.0.1:{port}" for port in ports]
    log_paths = [Path(f"{args.server_log}.{i}" if args.workers > 1 else args.server_log) for i in range(args.workers)]
    servers, log_files = [], []
    try:
        for port, log_path in zip(ports, log_paths):
            env = {**os.environ, "DATABASE_URL": args.database_url, "SESSION_SECRET": "load-test-secret", "DEV_LOGIN": "1",
                   "BASE_URL": f"http://127.0.0.1:{port}", "VAULT_TWINS_URL": f"http://127.0.0.1:{twin_port}",
                   "CATALOG_RATE_LIMIT": str(args.catalog_rate_limit),
                   "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))}
            for key in ("VERCEL", "VERCEL_URL"):
                env.pop(key, None)
            log_files.append(log_path.open("w", encoding="utf-8"))
            servers.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "--factory", "vault.app:create_app", "--host", "127.0.0.1", "--port", str(port),
                 "--log-level", "warning"], cwd=ROOT, env=env, stdout=log_files[-1], stderr=subprocess.STDOUT))
        for base in bases:
            wait_for(f"{base}/api/health")
        with httpx.Client(base_url=bases[0], timeout=60) as person:
            assert person.post("/api/auth/dev-login").status_code == 200
            up = person.post("/api/v1/imports", files={"file": ("load.csv", collection_csv(args.collection_rows), "text/csv")})
            assert up.status_code in (200, 201), up.text[:200]
            text = "\n".join(["Commander", f"1 {LOCAL_CARD}", "", "Deck"] + [f"1 Collected Card {i}" for i in range(98)])
            deck = person.post("/api/v1/decks", json={"name": "Load test deck", "text": text})
            assert deck.status_code == 201, deck.text[:200]
            token = person.post("/api/v1/me/tokens", json={"name": "load test", "scopes": ["read"]}).json()["token"]
        print(f"{args.workers} server instance(s) up; collection of {args.collection_rows} rows, deck {deck.json()['id']}", flush=True)
        failures = 0
        twin_api = f"http://127.0.0.1:{twin_port}/_twins/api"

        def phase(key: str, title: str, *, mix: str = "mixed", calls: int | None = None, archidekt: int | None = archidekt_id,
                  busy_ok: frozenset[str] = frozenset()) -> None:
            nonlocal failures
            if args.only and key not in args.only.split(","):
                return
            tally = run_load(bases, token, clients=args.clients, calls=calls or args.calls, burst=args.burst, card=LOCAL_CARD,
                             timeout=args.timeout, deck_id=deck.json()["id"], archidekt_id=archidekt, mix=mix, busy_ok=busy_ok)
            report(tally, title)
            failures += tally.bad()

        shape = f"{args.clients} agents x {args.calls} calls, {args.burst} at once each"
        slow_id = universe.archidekt.add_deck("Slow deck", "load-tester", [(1, "Sol Ring")])["id"]
        if not args.only or "wizards" in args.only.split(","):
            httpx.post(f"{twin_api}/wizards/outage", json={"on": True})  # first, while no instance has read the rules yet
            phase("wizards", "Wizards down, cold instances (rules answer 503 with a retry hint, the rest must be fine)",
                  calls=max(8, args.calls // 2), archidekt=None, busy_ok=frozenset({"get_rule", "search_rules"}))
            httpx.post(f"{twin_api}/wizards/outage", json={"on": False})
            time.sleep(FAILURE_TTL + 1)  # a failed read of the rules is remembered for that long: Wizards is up again, the instances do not know yet
        phase("mixed", f"mixed tools, {shape}")
        phase("catalog", f"catalog tools only, {shape}", mix="catalog")
        httpx.post(f"{twin_api}/archidekt/latency", json={"seconds": 2})
        phase("archidekt", "Archidekt answering in 2 s, deck not cached yet", calls=max(8, args.calls // 2), archidekt=slow_id)
        httpx.post(f"{twin_api}/archidekt/latency", json={"seconds": 0})
        return 1 if failures else 0
    finally:
        for server in servers:
            if os.name == "nt":  # terminate() on Windows can leave child processes running, and holding their connections
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
            else:
                server.terminate()
            try:
                server.wait(20)
            except subprocess.TimeoutExpired:
                server.kill()
        twins.should_exit = True
        for log_file in log_files:
            log_file.close()
        texts = [path.read_text(encoding="utf-8", errors="replace") for path in log_paths]
        print(f"\nserver logs {log_paths[0]}{' ...' if len(log_paths) > 1 else ''}: {sum(t.count('Traceback') for t in texts)} traceback(s), "
              f"{sum(t.count(chr(34) + 'event' + chr(34)) for t in texts)} structured event line(s)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default=os.environ.get("VAULT_URL"), help="a running Vault, e.g. https://mtgvault.cards")
    p.add_argument("--token", default=os.environ.get("VAULT_TOKEN"), help="a personal access token (read scope is enough)")
    p.add_argument("--local", action="store_true", help="start a Vault and the twins on this machine and test that")
    p.add_argument("--database-url", default=os.environ.get("LOAD_TEST_DATABASE_URL"), help="--local: a Postgres database that may be filled")
    p.add_argument("--workers", type=int, default=2, help="--local: server instances (separate processes, each with its own connection pool)")
    p.add_argument("--cards", type=int, default=5000, help="--local: size of the made-up card catalog")
    p.add_argument("--collection-rows", type=int, default=3000, help="--local: rows in the test collection")
    p.add_argument("--catalog-rate-limit", type=int, default=100000, help="--local: CATALOG_RATE_LIMIT a minute (high: measure the server, not the limit)")
    p.add_argument("--server-log", default=str(Path(os.environ.get("TEMP", "/tmp")) / "vault-load-test-server.log"), help="--local: where the server's log goes")
    p.add_argument("--only", help="--local: run only these phases, comma-separated: wizards, mixed, catalog, archidekt")
    p.add_argument("--clients", type=int, default=5, help="agents calling at the same time")
    p.add_argument("--calls", type=int, default=20, help="tool calls each agent makes")
    p.add_argument("--burst", type=int, default=2, help="calls each agent has in flight at once")
    p.add_argument("--mix", choices=("mixed", "catalog", "own"), default="mixed", help="remote: which tools (own = collection and decks)")
    p.add_argument("--card", default="Lightning Bolt", help="remote: a card the catalog knows")
    p.add_argument("--deck-id", type=int, help="remote: a saved deck of the token's person (adds get_deck, deck_stats, deck_legality)")
    p.add_argument("--archidekt-id", type=int, help="remote: a public Archidekt deck id (adds get_archidekt_deck: asks Archidekt)")
    p.add_argument("--timeout", type=float, default=60, help="seconds before a call counts as no answer")
    args = p.parse_args(argv)
    if args.local:
        if not args.database_url:
            p.error("--local needs --database-url (or LOAD_TEST_DATABASE_URL)")
        return local_run(args)
    if not (args.url and args.token):
        p.error("give --url and --token (or VAULT_URL and VAULT_TOKEN), or use --local")
    tally = run_load(args.url.rstrip("/"), args.token, clients=args.clients, calls=args.calls, burst=args.burst, mix=args.mix,
                     card=args.card, timeout=args.timeout, deck_id=args.deck_id, archidekt_id=args.archidekt_id)
    report(tally, f"{args.url}: {args.clients} agents x {args.calls} calls, {args.burst} at once each")
    return 1 if tally.bad() else 0


if __name__ == "__main__":
    sys.exit(main())

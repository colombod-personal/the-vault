"""Combos from Commander Spellbook, asked for on demand.

The Vault keeps no copy of their data (docs/data-sources.md): when a person asks about a deck, one call
goes to Commander Spellbook's public API and the answer is shortened to what an agent needs. Combo
descriptions are theirs (written by their community) and are shown as theirs, with a link to each
combo's page. A slow or failing upstream becomes a clear error; it never slows anything else.

Two guards protect them and us (docs/data-sources.md, Combos): a **rate limit** (at most ``RATE_PER_MINUTE`` calls a minute
from one server process, so a busy day cannot flood a volunteer-run service) and a **circuit breaker** (after
``BREAKER_FAILURES`` failures in a row the client stops calling for ``BREAKER_COOLDOWN`` seconds, then lets one probe call
through; a success closes it, a failure opens it again for twice as long, up to ``MAX_COOLDOWN``). Both are per process: each
serverless instance counts its own calls, so the real ceiling is the limit times the number of warm instances. A refused call
makes no request at all.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import httpx

URL = "https://backend.commanderspellbook.com/find-my-combos"
PAGE_URL = "https://commanderspellbook.com/combo/"
USER_AGENT = "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)"
TIMEOUT = 15.0
MAX_PER_GROUP = 10
MAX_CARDS = 600
RATE_PER_MINUTE = 20
BREAKER_FAILURES = 3
BREAKER_COOLDOWN = 60.0
MAX_COOLDOWN = 300.0


class ComboServiceError(RuntimeError):
    """Commander Spellbook could not answer (down, slow, or it refused the request)."""

    retry_after: int | None = None


class ComboServiceBusy(ComboServiceError):
    """The client did not even try: its own rate limit is reached, or its circuit breaker is open after repeated failures."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after + 0.999))


class UpstreamGuard:
    """A sliding-window rate limit and a circuit breaker for one upstream. ``clock`` is injectable for tests."""

    def __init__(self, rate_per_minute: int = RATE_PER_MINUTE, failures: int = BREAKER_FAILURES,
                 cooldown: float = BREAKER_COOLDOWN, max_cooldown: float = MAX_COOLDOWN, clock=time.monotonic):
        self.rate, self.threshold, self.base_cooldown, self.max_cooldown, self.clock = rate_per_minute, failures, cooldown, max_cooldown, clock
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()
        self._failures = 0
        self._cooldown = cooldown
        self._open_until = 0.0
        self._probing = False

    @property
    def state(self) -> str:
        with self._lock:
            if self._probing:
                return "half-open"
            if not self._open_until:
                return "closed"
            return "open" if self.clock() < self._open_until else "half-open"

    def before_call(self) -> None:
        """Count the call, or refuse it (ComboServiceBusy) when the breaker is open or the rate limit is reached."""
        with self._lock:
            now = self.clock()
            probe = False
            if self._open_until:
                if now < self._open_until or self._probing:
                    wait = (self._open_until - now) if now < self._open_until else 1.0
                    raise ComboServiceBusy("Commander Spellbook failed several times in a row, so the Vault is not calling it for a "
                                           f"moment (about {int(max(wait, 1))} s). Try again shortly.", wait)
                probe = True  # the cooldown is over: this one call decides
            while self._calls and now - self._calls[0] >= 60.0:
                self._calls.popleft()
            if len(self._calls) >= self.rate:
                raise ComboServiceBusy(f"The Vault already asked Commander Spellbook {self.rate} times in the last minute and "
                                       "limits itself to be kind to a community service. Try again shortly.", 60.0 - (now - self._calls[0]))
            self._calls.append(now)
            self._probing = self._probing or probe

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()
            self._failures, self._cooldown, self._open_until, self._probing = 0, self.base_cooldown, 0.0, False

    def success(self) -> None:
        with self._lock:
            self._failures, self._cooldown, self._open_until, self._probing = 0, self.base_cooldown, 0.0, False

    def failure(self, retry_after: float | None = None) -> None:
        """A failure counts toward the breaker; an upstream ``Retry-After`` opens it at once for that long."""
        with self._lock:
            self._failures += 1
            if self._probing:
                self._cooldown = min(self._cooldown * 2, self.max_cooldown)
            if self._probing or self._failures >= self.threshold or retry_after:
                wait = min(max(retry_after or 0.0, self._cooldown), self.max_cooldown)
                self._open_until = self.clock() + wait
                self._probing = False


GUARD = UpstreamGuard()


def ask(cards: list[tuple[str, int]], commanders: list[str], transport: httpx.BaseTransport | None = None) -> dict:
    body = {"main": [{"card": name, "quantity": qty} for name, qty in cards[:MAX_CARDS]],
            "commanders": [{"card": name, "quantity": 1} for name in commanders[:12]]}
    GUARD.before_call()
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
            res = client.post(URL, params={"limit": 1}, json=body)
    except httpx.HTTPError as exc:
        GUARD.failure()
        raise ComboServiceError("Commander Spellbook did not answer in time.") from exc
    if res.status_code != 200:
        if res.status_code >= 500 or res.status_code == 429:  # their trouble, or they ask us to slow down; another 4xx is about our request
            retry = res.headers.get("retry-after", "")
            GUARD.failure(float(retry) if retry.isdigit() else None)
        else:
            GUARD.success()  # the service answered: a refusal of this request says nothing about its health
        raise ComboServiceError(f"Commander Spellbook answered {res.status_code}.")
    try:
        results = res.json()["results"]
    except (ValueError, KeyError, TypeError) as exc:
        GUARD.failure()
        raise ComboServiceError("Commander Spellbook's answer was not in the expected shape.") from exc
    GUARD.success()
    return results


def _variant(v: dict, have: set[str]) -> dict:
    cards = [u["card"]["name"] for u in v.get("uses", []) if u.get("card")]
    return {
        "id": v["id"], "url": PAGE_URL + str(v["id"]), "cards": cards,
        "missing": [c for c in cards if c.lower() not in have],
        "produces": [p["feature"]["name"] for p in v.get("produces", []) if p.get("feature")],
        "description": v.get("description") or "", "mana_needed": v.get("manaNeeded") or None,
        "prerequisites": " ".join(x for x in (v.get("easyPrerequisites"), v.get("notablePrerequisites")) if x) or None,
        "color_identity": v.get("identity"), "popularity": v.get("popularity"), "bracket_tag": v.get("bracketTag"),
    }


def two_card_combos(results: dict) -> list[dict]:
    """The combos in the deck that use exactly two cards and that Commander Spellbook says are infinite or win the game (what
    the Commander Brackets restrict). Uncapped; each carries Spellbook's own bracket tag as given, which is theirs, not Wizards'."""
    found = []
    for v in results.get("included") or []:
        cards = list(dict.fromkeys(u["card"]["name"] for u in v.get("uses", []) if u.get("card")))
        produces = [p["feature"]["name"] for p in v.get("produces", []) if p.get("feature")]
        if len(cards) == 2 and any("infinite" in name.lower() or name.lower() == "win the game" for name in produces):
            found.append({"cards": cards, "produces": produces[:6], "url": PAGE_URL + str(v["id"]), "bracket_tag": v.get("bracketTag"),
                          "mana_needed": v.get("manaNeeded") or None, "source": "Commander Spellbook"})
    return found


def listed_card_names(results: dict) -> list[list[str]]:
    """The card names of every combo Commander Spellbook lists as in the deck (uncapped), for leaving to them what they already list."""
    return [[u["card"]["name"] for u in v.get("uses", []) if u.get("card")] for v in results.get("included") or []]


def summarize(results: dict, deck_names: set[str]) -> dict:
    """The combos in the deck, and the ones a card short, shortened and capped."""
    have = {n.lower() for n in deck_names}
    groups = {"included": results.get("included") or [], "almost_included": results.get("almostIncluded") or []}
    out = {name: [_variant(v, have) for v in found[:MAX_PER_GROUP]] for name, found in groups.items()}
    return {**out, "totals": {name: len(found) for name, found in groups.items()},
            "notes": ["Combos and their descriptions are Commander Spellbook's, written by its community; check each on its page before relying on it.",
                      "'almost_included' combos need the cards listed under 'missing'. Other groups (other commanders, more colors) are not shown."]}

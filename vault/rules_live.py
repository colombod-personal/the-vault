"""The Comprehensive Rules, read live from Wizards of the Coast: nothing of the rules is stored (docs/rules-index.md).

Each instance reads Wizards' rules page (at most every ``PAGE_TTL`` seconds) to find the current edition's TXT, fetches
and parses it (``vault.rules_parser``) when the edition changes (a new file name) **or when the file under the same name changed** (a silent
correction: one ``HEAD`` asks for its ``ETag`` or ``Last-Modified``), and keeps in memory: the rules, a search index (BM25),
and a navigation map (the hierarchy with headings, the cross-references both ways, glossary terms and keyword abilities
mapped to their rules). If Wizards cannot be reached and nothing is cached, :class:`RulesUnavailable` is raised: the
tools say so and never answer from memory.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

import httpx

from . import provenance as prov
from . import rules_parser

RULES_PAGE = "https://magic.wizards.com/en/rules"
USER_AGENT = "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)"
PAGE_TTL = 6 * 3600
FAILURE_TTL = 20  # seconds a failed read is remembered when no edition is cached: calls answer 503 at once instead of each waiting for Wizards
TIMEOUT = httpx.Timeout(20, connect=5)
log = logging.getLogger("vault.access")
TXT_LINK = re.compile(r'https://media\.wizards\.com/[^"\'<>]+?MagicCompRules[^"\'<>]*?\.txt', re.IGNORECASE)
REF = re.compile(r"\b(\d{3}\.\d+[a-z]?|\d{3})\b")
WORD = re.compile(r"[a-z0-9']+")
STOP = {"the", "and", "for", "that", "with", "this", "from", "are", "can", "does", "what", "when", "how", "its", "has",
        "have", "was", "will", "you", "your", "into", "onto", "any", "all", "not", "but", "they", "their", "then", "than"}


class RulesUnavailable(Exception):
    """Wizards' rules could not be fetched and no edition is cached. ``retry_after`` is when to ask again, in seconds."""

    def __init__(self, message: str, retry_after: int = FAILURE_TTL):
        super().__init__(message)
        self.retry_after = retry_after


def _validator(res: httpx.Response) -> str | None:
    return res.headers.get("etag") or res.headers.get("last-modified")


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if len(w) > 2 and w not in STOP]


@dataclass
class Edition:
    version: str
    url: str
    rows: dict[str, dict]
    order: list[str]
    children: dict[str, list[str]] = field(default_factory=dict)
    cites: dict[str, list[str]] = field(default_factory=dict)
    cited_by: dict[str, list[str]] = field(default_factory=dict)
    terms: dict[str, dict] = field(default_factory=dict)  # lower-case glossary term or keyword -> where it is defined
    tf: dict[str, Counter] = field(default_factory=dict)
    df: Counter = field(default_factory=Counter)
    avg_len: float = 1.0
    validator: str | None = None  # the file's ETag (else Last-Modified) when it was read: how a silent correction is noticed

    @classmethod
    def build(cls, text: str, url: str) -> "Edition":
        parsed = rules_parser.parse(text)
        rows = {r["number"]: r for r in parsed.rules}
        ed = cls(parsed.version, url, rows, [r["number"] for r in parsed.rules])
        for r in parsed.rules:
            if r["parent"]:
                ed.children.setdefault(r["parent"], []).append(r["number"])
        for number, r in rows.items():
            refs = [m for m in dict.fromkeys(REF.findall(r["text"])) if m in rows and m != number and m != r["parent"]]
            if refs:
                ed.cites[number] = refs
                for m in refs:
                    ed.cited_by.setdefault(m, []).append(number)
        for number, r in rows.items():  # keyword abilities and actions: "702.19 Trample", "701.21 Sacrifice"
            if r["kind"] == "rule" and re.fullmatch(r"70[12]\.\d+", number) and len(r["text"]) <= 60 and "." not in r["text"]:
                ed.terms.setdefault(r["text"].lower(), {"term": r["text"], "rules": [], "glossary": None})["rules"].append(number)
        for number, r in rows.items():
            if r["kind"] == "glossary":
                term = number.split(":", 1)[1]
                entry = ed.terms.setdefault(term.lower(), {"term": term, "rules": [], "glossary": None})
                entry["glossary"] = number
                entry["rules"] = list(dict.fromkeys(entry["rules"] + ed.cites.get(number, [])))
        docs = {n: _words(r["text"]) for n, r in rows.items() if r["kind"] != "heading"}
        ed.tf = {n: Counter(w) for n, w in docs.items()}
        ed.df = Counter(w for words in docs.values() for w in set(words))
        ed.avg_len = sum(map(len, docs.values())) / max(1, len(docs))
        return ed

    # -- reading -------------------------------------------------------------------------------------
    def find(self, number: str) -> dict | None:
        number = number.strip()
        if number in self.rows:
            return self.rows[number]
        lower = number.lower()
        return next((r for n, r in self.rows.items() if n.lower() == lower), None)

    def brief(self, number: str | None) -> dict | None:
        r = self.rows.get(number) if number else None
        return {"number": r["number"], "text": r["text"].split("\n")[0][:200], "kind": r["kind"]} if r else None

    def rule(self, number: str) -> dict | None:
        """One rule with where it sits: parent, children, previous and next sibling, what it cites and what cites it."""
        r = self.find(number)
        if r is None:
            return None
        n = r["number"]
        siblings = self.children.get(r["parent"], []) if r["parent"] else [x for x in self.order if self.rows[x]["parent"] is None
                                                                        and self.rows[x]["kind"] == r["kind"]]
        i = siblings.index(n) if n in siblings else -1
        return {**r, "parent": self.brief(r["parent"]),
                "children": [self.brief(c) for c in self.children.get(n, [])],
                "previous": self.brief(siblings[i - 1]) if i > 0 else None,
                "next": self.brief(siblings[i + 1]) if 0 <= i < len(siblings) - 1 else None,
                "cites": [self.brief(c) for c in self.cites.get(n, [])],
                "cited_by": [self.brief(c) for c in self.cited_by.get(n, [])[:30]]}

    def outline(self, number: str | None = None) -> dict:
        """The table of contents: the sections, or what is directly under ``number``, with headings."""
        if not number:
            items = [x for x in self.order if self.rows[x]["parent"] is None and self.rows[x]["kind"] == "heading"]
            return {"under": None, "items": [self.brief(x) for x in items]}
        r = self.find(number)
        if r is None:
            return {"under": number, "items": [], "unknown": True}
        return {"under": self.brief(r["number"]), "items": [self.brief(c) for c in self.children.get(r["number"], [])]}

    def term(self, name: str) -> dict | None:
        entry = self.terms.get(name.strip().lower())
        if entry is None:
            return None
        g = self.rows.get(entry["glossary"]) if entry["glossary"] else None
        return {"term": entry["term"], "glossary": {"number": g["number"], "text": g["text"]} if g else None,
                "rules": [self.brief(n) for n in entry["rules"]]}

    def search(self, query: str, limit: int = 5) -> tuple[list[dict], str]:
        words = _words(query)
        if not words:
            return [], "all words"
        n, k1, b = len(self.tf), 1.2, 0.75
        scores: dict[str, float] = defaultdict(float)
        for num, counts in self.tf.items():
            length = sum(counts.values())
            for w in words:
                if w in counts:
                    idf = math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                    scores[num] += idf * counts[w] * (k1 + 1) / (counts[w] + k1 * (1 - b + b * length / self.avg_len))
        lower = " " + " ".join(WORD.findall(query.lower())) + " "
        for term, entry in self.terms.items():  # a keyword or glossary term in the question: its rules first
            if f" {term} " in lower:
                for rule_number in entry["rules"]:
                    for x in [rule_number] + self.children.get(rule_number, []):
                        if x in self.tf:
                            scores[x] += 5.0
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        results = [{"number": x, "text": self.rows[x]["text"], "kind": self.rows[x]["kind"], "parent": self.rows[x]["parent"]}
                   for x, _ in ranked]
        matched = "all words" if results and all(w in self.tf[results[0]["number"]] for w in words) else "any word"
        return results, matched

    def in_force(self) -> bool:
        """False while the edition's own 'effective as of' date is still ahead: Wizards can publish a file weeks before it takes effect."""
        return date.fromisoformat(self.version) <= date.today()

    def provenance(self) -> list:
        later = "" if self.in_force() else f"; this edition takes effect on {self.version}, until then the previous edition is in force"
        return [prov.source("Wizards of the Coast", origin=f"Comprehensive Rules (read live; the Vault stores no copy{later})",
                            url=self.url, as_of=date.today(), version=self.version, wizards_material=True)]


class LiveRules:
    """The current edition, fetched from Wizards when needed and cached in memory per instance.

    Reading Wizards is the one slow thing the rules tools can do (a page and a file of about a megabyte), so: one call
    reads it while the others wait for that read only when there is nothing to serve; a cached edition that is due for a
    check is served to everyone else meanwhile (never made to wait); and a failed read with nothing cached is remembered
    for ``FAILURE_TTL`` seconds, so a Wizards that is down or slow costs one attempt per window and not one per call (ten
    parallel calls used to mean ten timeouts in a row behind a lock, holding every thread the server had)."""

    def __init__(self, transport: httpx.BaseTransport | None = None, page_url: str = RULES_PAGE, page_ttl: float = PAGE_TTL,
                 clock=time.monotonic):
        self.transport, self.page_url, self.page_ttl, self.clock = transport, page_url, page_ttl, clock
        self._edition: Edition | None = None
        self._checked = -math.inf
        self._failed_until = -math.inf
        self._failure = ""
        self._lock = threading.Lock()

    def reset(self, transport: httpx.BaseTransport | None = None) -> None:
        """Forget the cached edition (and use ``transport`` from now on): the next call reads Wizards again."""
        with self._lock:
            self.transport, self._edition, self._checked, self._failed_until = transport, None, -math.inf, -math.inf

    @property
    def cached_version(self) -> str | None:
        """The edition already read, without fetching (None until a rules tool was first used on this instance)."""
        return self._edition.version if self._edition else None

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=TIMEOUT, follow_redirects=True,
                            headers={"User-Agent": USER_AGENT, "Accept": "*/*"})

    @staticmethod
    def _read(client: httpx.Client, url: str) -> Edition:
        res = client.get(url)
        res.raise_for_status()
        edition = Edition.build(res.content.decode("utf-8-sig"), url)
        edition.validator = _validator(res)
        return edition

    def edition(self) -> Edition:
        edition = self._edition
        if edition is not None and self.clock() - self._checked < self.page_ttl:
            return edition
        if edition is not None:
            if not self._lock.acquire(blocking=False):
                return edition  # someone is checking for a newer one: the one we have is good meanwhile
        else:
            self._lock.acquire()  # nothing to serve: wait for the read that is under way (or make it)
        try:
            if self._edition is not None and self.clock() - self._checked < self.page_ttl:
                return self._edition
            if self._edition is None and self.clock() < self._failed_until:
                raise RulesUnavailable(self._failure, max(1, math.ceil(self._failed_until - self.clock())))
            try:
                with self._client() as client:
                    page = client.get(self.page_url)
                    page.raise_for_status()
                    links = TXT_LINK.findall(page.text)
                    if not links:
                        raise RulesUnavailable("Wizards' rules page has no link to the rules text")
                    url = links[0].replace(" ", "%20")
                    if self._edition is None or self._edition.url != url:
                        self._edition = self._read(client, url)
                    else:  # the same file name: Wizards can correct a file in place, and only the file itself says so
                        head = client.head(url)
                        head.raise_for_status()
                        validator = _validator(head)
                        if validator and self._edition.validator and validator != self._edition.validator:
                            self._edition = self._read(client, url)
                self._checked = self.clock()
            except Exception as exc:  # whatever went wrong with Wizards' side: a download, an encoding, a format we do not know
                log.warning(json.dumps({"event": "rules_read_failed", "error_class": type(exc).__name__,
                                        "cached_edition": self._edition is not None}))
                if self._edition is None:
                    self._failure = "The Comprehensive Rules could not be read from Wizards of the Coast just now. Try again shortly."
                    self._failed_until = self.clock() + FAILURE_TTL
                    raise RulesUnavailable(self._failure) from exc
                self._checked = self.clock() - self.page_ttl + 300  # keep the cached edition; try again in 5 minutes
            return self._edition
        finally:
            self._lock.release()

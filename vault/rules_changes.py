"""What changed between two editions of the Comprehensive Rules, worked out when someone asks and kept nowhere (#107).

The Vault stores no rules (docs/rules-index.md), so there is no table of editions to diff. Wizards' CDN does keep every
earlier edition under its dated file name (docs/reconciler-design.md), and ``vault.rules_live.LiveRules.compare`` reads the
current and the previous one, hands them to :func:`diff`, and keeps only the small result, in memory, for a few hours. This
module is the pure part: the diff, the capped brief, and the check of which rule numbers a skill or an agent cites.

Everything here is deterministic: rules are listed in rule-number order, a sentence is the text up to the next full stop, and
the same two editions always give the same brief.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
PREVIEW = 160  # characters of a rule shown for an added or removed rule
SENTENCE_CAP = 240  # characters of the first changed sentence shown for a changed rule
DEFAULT_LIMIT, MAX_LIMIT = 25, 50
MIN_MOVED_TEXT = 25  # a rule this short is not matched to another by its text alone (headings repeat)
DATED_NAME = re.compile(r"^(?P<head>.*/)(?P<year>\d{4})/downloads/(?P<stem>MagicCompRules)(?P<sep>%20| )(?P<date>\d{8})\.txt$")


def number_key(number: str) -> tuple:
    """Rule order: sections, subsections and rules by number (702.19b after 702.19a), glossary entries last by name."""
    if number.startswith("glossary:"):
        return (1, number.lower(), 0, "")
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?([a-z]?)", number)
    return (0, int(m.group(1)), int(m.group(2) or -1), m.group(3)) if m else (2, number, 0, "")


def squash(text: str) -> str:
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text) if s.strip()]


def preview(text: str, cap: int = PREVIEW) -> str:
    first = sentences(text)[0] if sentences(text) else ""
    return first if len(first) <= cap else first[: cap - 1].rstrip() + "…"


def first_change(old: str, new: str) -> dict:
    """The first sentence of ``new`` that ``old`` does not have (``now``), and the first of ``old`` that ``new`` lost (``was``).
    Either is None when nothing was added or nothing was lost (a rule that only grew, or only lost a sentence)."""
    old_s, new_s = sentences(old), sentences(new)
    old_set, new_set = set(old_s), set(new_s)
    now = next((s for s in new_s if s not in old_set), None)
    was = next((s for s in old_s if s not in new_set), None)
    at = 0
    if now is not None and was is not None:  # a long list with one word added: show the words around the difference, not the start
        at = next((i for i, (a, b) in enumerate(zip(was, now)) if a != b), min(len(was), len(now)))
    return {"was": window(was, at), "now": window(now, at)}


def window(sentence: str | None, at: int, cap: int = SENTENCE_CAP) -> str | None:
    """``sentence`` cut to ``cap`` characters around position ``at`` (the first character that differs), with … where it was cut."""
    if sentence is None or len(sentence) <= cap:
        return sentence
    start = max(0, min(at - cap // 3, len(sentence) - cap))
    end = start + cap
    return ("…" if start else "") + sentence[start:end].strip() + ("…" if end < len(sentence) else "")


RULE_REF = re.compile(r"\b\d{3}(?:\.\d+[a-z]*)?\b")


def words_of(text: str) -> str:
    """A rule's words for telling whether it moved: whitespace squashed and the rule numbers it cites blanked, because the rules
    after an inserted one change the numbers they cite as well (rule 506.7c cited 506.7, its successor cites 506.8)."""
    return RULE_REF.sub("#", squash(text))


@dataclass
class Changes:
    """The complete difference between two editions (every rule number; :meth:`brief` caps it for an answer)."""

    old_version: str
    new_version: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # the number no longer exists and its text is nowhere else
    renumbered: dict[str, str] = field(default_factory=dict)  # old number -> new number: the same words, found once in each edition
    shifted: dict[str, str] = field(default_factory=dict)  # a number both editions have -> the old number whose words it holds now
    gone: set[str] = field(default_factory=set)  # old numbers the new edition does not have at all: what a citation can no longer rely on
    changed: dict[str, dict] = field(default_factory=dict)  # number -> {"was", "now"}
    old_text: dict[str, str] = field(default_factory=dict)  # a short preview of each removed or renumbered-away rule
    new_text: dict[str, str] = field(default_factory=dict)  # a short preview of each added rule
    headings: dict[str, str] = field(default_factory=dict)  # section and subsection titles of the new edition

    def counts(self) -> dict:
        return {"added": len(self.added), "removed": len(self.removed), "renumbered": len(self.renumbered),
                "shifted": len(self.shifted), "changed": len(self.changed)}

    def by_subsection(self, top: int = 10) -> list[dict]:
        tally: Counter = Counter("glossary" if n.startswith("glossary:") else n[:3] for n in
                                 [*self.added, *self.removed, *self.renumbered, *self.changed])
        ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0] == "glossary", kv[0]))[:top]
        return [{"section": s, "heading": self.headings.get(s), "changes": n} for s, n in ranked]

    def brief(self, limit: int = DEFAULT_LIMIT) -> dict:
        """The change brief for an answer: counts, then each list in rule order and capped at ``limit``."""
        limit = max(1, min(limit, MAX_LIMIT))
        added = sorted(self.added, key=number_key)
        removed = sorted(self.removed, key=number_key)
        moved = sorted(self.renumbered, key=number_key)
        shifted = sorted(self.shifted, key=number_key)
        changed = sorted(self.changed, key=number_key)
        return {
            "counts": self.counts(),
            "added": [{"number": n, "text": self.new_text.get(n, "")} for n in added[:limit]],
            "removed": [{"number": n, "text": self.old_text.get(n, "")} for n in removed[:limit]],
            "renumbered": [{"from": n, "to": self.renumbered[n]} for n in moved[:limit]],
            "shifted": [{"number": n, "holds_the_words_of": self.shifted[n]} for n in shifted[:limit]],
            "changed": [{"number": n, **self.changed[n]} for n in changed[:limit]],
            "capped": any(len(x) > limit for x in (added, removed, moved, shifted, changed)),
            "limit": limit,
            "by_subsection": self.by_subsection(),
        }


def diff(old, new) -> Changes:
    """Compare two editions (anything with ``version`` and ``rows``: number -> {"text", "kind", ...}).

    Wizards inserts rules, and the ones after them take new numbers. A rule whose exact words (long enough to be its own, and
    found once in each edition) sit under another number in the new edition has *moved* (``renumbered``, old number to new).
    A number both editions have that now holds the words of another rule is ``shifted``, not ``changed``. What is left is
    ``added`` (a number only the new edition has, not explained by a move), ``removed`` (a number only the old one had, its
    words nowhere else) and ``changed`` (the same number with other words), each with its first changed sentence."""
    old_rows, new_rows = old.rows, new.rows
    changes = Changes(old.version, new.version)
    where_old: dict[str, list[str]] = {}
    where_new: dict[str, list[str]] = {}
    for n, row in old_rows.items():
        where_old.setdefault(words_of(row["text"]), []).append(n)
    for n, row in new_rows.items():
        where_new.setdefault(words_of(row["text"]), []).append(n)
    moves: dict[str, str] = {}
    for text, sources in where_old.items():
        targets = where_new.get(text, [])
        if (len(text) >= MIN_MOVED_TEXT and len(sources) == 1 and len(targets) == 1 and sources[0] != targets[0]
                and old_rows[sources[0]]["kind"] == new_rows[targets[0]]["kind"]):
            moves[sources[0]] = targets[0]
    taken = {m: n for n, m in moves.items()}  # new number -> the old number whose words it holds
    for n, row in new_rows.items():
        if row["kind"] == "heading" and "." not in n:
            changes.headings[n] = row["text"]
        if n not in old_rows:
            if n not in taken:
                changes.added.append(n)
                changes.new_text[n] = preview(row["text"])
        elif squash(old_rows[n]["text"]) != squash(row["text"]):
            if n in taken:
                changes.shifted[n] = taken[n]
            else:
                changes.changed[n] = first_change(old_rows[n]["text"], row["text"])
    for n, row in old_rows.items():
        if n in moves:
            changes.renumbered[n] = moves[n]
        if n not in new_rows:
            changes.gone.add(n)
            if n not in moves:
                changes.removed.append(n)
            changes.old_text[n] = preview(row["text"])
    return changes


# -- which rule numbers do the skills and agents cite? ---------------------------------------------------------------------------

NUMBER = r"\d{3}(?:\.\d+[a-z]?)?"
AFTER_RULE = re.compile(rf"(?i)\b(?:rules?|CR)\s+({NUMBER}(?:\s*(?:,|and|or|to|-|–|/)\s*{NUMBER})*)")
DOTTED = re.compile(r"(?<![\w$.])(\d{3}\.\d+[a-z]?)(?!\w)")
CITED_FILES = ("skills/*/SKILL.md", "agents/*.md")


@dataclass(frozen=True)
class Citation:
    path: str
    line: int
    number: str


def cited_numbers(text: str) -> list[tuple[int, str]]:
    """Rule numbers a text cites, as (line, number): ``rule 603.3b``, ``rules 506 to 511``, ``CR 702.19``, or any dotted number
    like ``613.1a``. A bare three-digit number is a rule only after the word rule (or CR): ``100-card`` is not."""
    found: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        numbers = [n for m in AFTER_RULE.finditer(line) for n in re.findall(NUMBER, m.group(1))]
        numbers += DOTTED.findall(line)
        for n in dict.fromkeys(numbers):
            found.append((i, n))
    return found


def scan(root: Path, patterns: tuple[str, ...] = CITED_FILES) -> list[Citation]:
    out = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            out += [Citation(rel, line, n) for line, n in cited_numbers(path.read_text(encoding="utf-8"))]
    return out


def check(citations: list[Citation], current, changes: Changes | None) -> dict:
    """Sort citations against the current edition and what changed since the previous one.

    ``failures``: the number is not in the current edition (removed or renumbered since the previous one, or never a rule).
    ``warnings``: the number is still there with other words, so the sentence that cites it may no longer be true."""
    failures, warnings = [], []
    gone = changes.gone if changes else set()
    for c in citations:
        if c.number not in current.rows:
            why = (f"removed from the rules after the {changes.old_version} edition" if changes and c.number in changes.removed
                   else f"renumbered after the {changes.old_version} edition (now {changes.renumbered[c.number]})" if c.number in gone
                   else "not a rule number in the current edition")
            failures.append({"path": c.path, "line": c.line, "number": c.number, "problem": why})
        elif changes and c.number in changes.changed:
            warnings.append({"path": c.path, "line": c.line, "number": c.number,
                             "problem": f"changed since the {changes.old_version} edition", **changes.changed[c.number]})
        elif changes and c.number in changes.shifted:
            warnings.append({"path": c.path, "line": c.line, "number": c.number, "was": None, "now": None,
                             "problem": f"now holds the words that were rule {changes.shifted[c.number]} in the {changes.old_version} edition"})
    return {"cited": len(citations), "numbers": len({c.number for c in citations}), "failures": failures, "warnings": warnings,
            "current_version": current.version, "previous_version": changes.old_version if changes else None}


def report(result: dict) -> str:
    """The check as text for a job summary or a terminal."""
    lines = [f"Rule citations in skills and agents: {result['cited']} citations of {result['numbers']} rule numbers, "
             f"checked against the Comprehensive Rules of {result['current_version']}"
             + (f" and the changes since {result['previous_version']}." if result["previous_version"] else
                " (no earlier edition was found, so only unknown numbers can be reported).")]
    for f in result["failures"]:
        lines.append(f"FAIL {f['path']}:{f['line']} rule {f['number']}: {f['problem']}")
    for w in result["warnings"]:
        lines.append(f"WARN {w['path']}:{w['line']} rule {w['number']}: {w['problem']}"
                     + (f"; now: {w['now']}" if w.get("now") else "") + (f"; was: {w['was']}" if w.get("was") else ""))
    if not result["failures"] and not result["warnings"]:
        lines.append("Every cited rule exists and is unchanged.")
    return "\n".join(lines)


def parse_date(value: str | None) -> date | None:
    """A date as YYYY-MM-DD or YYYYMMDD (the form in Wizards' file names); None when empty. Raises ValueError otherwise."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text) if "-" in text else datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"{value!r} is not a date: use YYYY-MM-DD") from exc

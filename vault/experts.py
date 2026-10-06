"""The expert council for connectors (issue #220): who sits on the panel, and each member's brief.

Where the plugin is installed (Claude Code, Claude desktop with plugins, Copilot, Cursor, Codex), the experts are
real agents. A connector (claude.ai, ChatGPT) can carry only tools, so ``council_brief`` hands the assistant the
panel the council's rules seat and every member's full brief, from the same files the plugin is built from
(``vault.experts_data``, generated from ``agents/*.md`` and ``skills/expert-council/SKILL.md``). The assistant then
gives each member's view in turn; the brief says so, because in one chat the views are not independent.

Seating (skills/expert-council/SKILL.md): always the judge and the devil's advocate; only the format expert(s) for
the format discussed (Commander adds the casual table; Two-Headed Giant adds the expert for the format the team
plays); the synergy and collection analysts when the goal calls for them; four to six members.
"""

from __future__ import annotations

from .experts_data import DATA

ALWAYS = ("vault-judge", "vault-devils-advocate")
FORMAT_EXPERTS = {
    "commander": ("vault-commander-expert", "vault-casual-table"),
    "limited": ("vault-limited-expert",), "draft": ("vault-limited-expert",), "sealed": ("vault-limited-expert",),
    "pauper": ("vault-pauper-expert",),
    "standard": ("vault-standard-expert",),
    "pioneer": ("vault-pioneer-expert",),
    "two-headed-giant": ("vault-two-headed-giant-expert",),
}
ALIASES = {"edh": "commander", "2hg": "two-headed-giant", "two headed giant": "two-headed-giant",
           "two_headed_giant": "two-headed-giant", "twoheadedgiant": "two-headed-giant"}
SYNERGY_GOALS = ("synerg", "tune", "explain", "improve", "plan", "review", "upgrade")
COLLECTION_GOALS = ("budget", "own", "buy", "cost", "money", "cheap", "missing", "collection")
MAX_MEMBERS = 6


def normal(fmt: str | None) -> str | None:
    if not fmt:
        return None
    key = " ".join(fmt.strip().lower().replace("_", " ").split())
    key = ALIASES.get(key, ALIASES.get(key.replace(" ", ""), key))
    return key.replace(" ", "-")


def seat(fmt: str | None, goal: str | None = None, team_format: str | None = None, budget: bool = False) -> list[dict]:
    """The panel: ``[{"id", "why"}]`` in speaking order (format experts, analysts, judge, devil's advocate last)."""
    fmt, team = normal(fmt), normal(team_format)
    goal_text = (goal or "").lower()
    panel: list[dict] = []
    for expert in FORMAT_EXPERTS.get(fmt or "", ()):
        why = ("the casual table: how the deck feels to play with friends" if expert == "vault-casual-table"
               else f"the {fmt.replace('-', ' ')} expert")
        panel.append({"id": expert, "why": why})
    if fmt == "two-headed-giant" and team in FORMAT_EXPERTS and team != "two-headed-giant":
        panel.append({"id": FORMAT_EXPERTS[team][0], "why": f"the {team} expert, for the format the team plays"})
    if not goal_text or any(w in goal_text for w in SYNERGY_GOALS):
        panel.append({"id": "vault-synergy-analyst", "why": "what the deck is trying to do and what works together"})
    if budget or any(w in goal_text for w in COLLECTION_GOALS):
        panel.append({"id": "vault-collection-analyst", "why": "what the person owns and what changes would cost"})
    room = MAX_MEMBERS - len(ALWAYS)
    panel = panel[:room]
    panel.append({"id": "vault-judge", "why": "any rules point, with checked citations"})
    panel.append({"id": "vault-devils-advocate", "why": "challenges every view, with evidence"})
    return panel


def brief(expert: str) -> dict | None:
    found = DATA["experts"].get(expert)
    return {"id": expert, **found} if found else None


def council(fmt: str | None, goal: str | None = None, team_format: str | None = None, budget: bool = False,
            format_note: str | None = None) -> dict:
    """Everything an assistant needs to chair the council in one chat."""
    key = normal(fmt)
    members = [{**m, **brief(m["id"])} for m in seat(key, goal, team_format, budget)]
    out = {
        "format": key,
        "goal": goal,
        "panel": members,
        "chair": DATA["chair"]["brief"],
        "how_to_run": ("You are the chair. Gather the shared facts once with the tools the chair's procedure names, then "
                       "give each member's view in turn, labelled with the member's name and following that member's brief "
                       "(at most three points each, every point tied to a tool result). Then let the devil's advocate "
                       "challenge them with evidence, and answer with the plan first (checked with validate_deck_changes), "
                       "then Agreed, Disputed and Not checked. In one chat the views are not independent: say so once."),
    }
    if key and key not in FORMAT_EXPERTS:
        out["note"] = (f"There is no {key} expert yet: the panel runs without a format expert, and says so.")
    elif not key:
        out["note"] = "No format given: the panel has no format expert. Ask the format, or read it from the deck."
    if format_note:
        out["format_note"] = format_note
    return out


def roster() -> list[dict]:
    return [{"id": name, "description": e["description"]} for name, e in sorted(DATA["experts"].items())]

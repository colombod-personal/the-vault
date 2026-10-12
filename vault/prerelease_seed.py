"""The manual seed of the prerelease guide table (vault-064.10.8): the sets released in the two years before 2026-10-12 and the
next announced set, so the table has something on day one.

Every URL below was opened on 2026-10-12 and answered 200, and every fact was read from the page named beside it (the guide's
list of what a Prerelease Pack holds, the WPN event page's scheduling line, the WPN announcement for Two-Headed Giant). A person
(or the builder session) read them; no page text is kept here, only links and short facts in the Vault's own words. Dates are in
the dataset the way the WPN page gives them (Friday start, seven days). Where a page does not state a fact the row says
``unverified`` and why.

``seed_rows()`` is what the migration inserts; :func:`apply_seed` inserts the rows that are missing (never touching a row that
exists, so a discovery result or a later correction is not undone).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

CHECKED = datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc)  # the day the pages below were opened and read (the UTC hour is approximate)
GUIDE = "https://magic.wizards.com/en/news/feature/"
WPN = "https://wpn.wizards.com/en/event/"
TWO_HG_ANNOUNCEMENT = "https://wpn.wizards.com/en/news/two-prerelease-changes-coming-with-reality-fracture"

STANDARD_PACK = "One Prerelease Pack per player; each pack holds six Play Boosters, a foil promo card, a deck box and a spindown die."


def _traditional(source: str, summary: str, *, play: int = 6, other: int = 0, status: str = "verified", note: str | None = None) -> dict:
    return {"unit": "player", "prerelease_packs": 1, "loose_play_boosters": 0, "play_boosters_in_each_pack": play,
            "other_boosters_in_each_pack": other, "summary": summary, "source_url": source,
            "fetched_at": CHECKED.isoformat(), "status": status, **({"note": note} if note else {})}


def _two_headed_giant(source: str, summary: str, *, packs: int, loose: int, status: str = "verified", note: str | None = None,
                      corroborated_by: list[str] | None = None) -> dict:
    return {"unit": "team", "prerelease_packs": packs, "loose_play_boosters": loose, "summary": summary, "source_url": source,
            "fetched_at": CHECKED.isoformat(), "status": status, **({"note": note} if note else {}),
            **({"corroborated_by": corroborated_by} if corroborated_by else {})}


OLD_2HG_NOTE = ("Wizards' announcement of 2026-07-17 says each team 'currently' receives two Prerelease Packs; it does not name "
                "this set, and this set's own event page does not state the contents.")


def _old_two_headed_giant() -> dict:
    return _two_headed_giant(TWO_HG_ANNOUNCEMENT, "Two Prerelease Packs per team (the arrangement before Reality Fracture).",
                             packs=2, loose=0, status="unverified", note=OLD_2HG_NOTE)


# The pages' own <title>, as read on 2026-10-12: the label a link carries (a heading, not guide text).
_TITLES = {
    "fdn": ("Magic: The Gathering Foundations Prerelease Guide", "Prerelease: Magic: The Gathering Foundations | WPN"),
    "dft": ("The Guide to Your First Prerelease with Aetherdrift", "Prerelease: Aetherdrift | WPN"),
    "tdm": ("Tarkir: Dragonstorm Prerelease Guide: What You Need to Know", "Prerelease – Tarkir: Dragonstorm | WPN"),
    "fin": ("Magic: The Gathering®—FINAL FANTASY™ Prerelease Guide", "Prerelease – Magic: The Gathering®—FINAL FANTASY | WPN"),
    "eoe": ("Edge of Eternities Prerelease Guide", "Prerelease: Edge of Eternities | WPN"),
    "spm": ("Magic: The Gathering® | Marvel's Spider-Man Prerelease Guide", "Prerelease: Magic: The Gathering® | Marvel’s Spider-Man | WPN"),
    "tla": ("Magic: The Gathering® | Avatar: The Last Airbender™ Prerelease Guide",
            "Prerelease – Magic: The Gathering® | Avatar: The Last Airbender™ | WPN"),
    "ecl": ("Lorwyn Eclipsed Prerelease Guide", "Prerelease – Lorwyn Eclipsed | WPN"),
    "tmt": ("Magic: The Gathering® | Teenage Mutant Ninja Turtles Prerelease Guide",
            "Prerelease – Magic: The Gathering® | Teenage Mutant Ninja Turtles | WPN"),
    "sos": ("Secrets of Strixhaven Prerelease Guide", "Prerelease – Secrets of Strixhaven | WPN"),
    "msh": ("Magic: The Gathering® | Marvel Super Heroes Prerelease Guide", "Prerelease - Magic: The Gathering® | Marvel Super Heroes | WPN"),
    "hob": ("Magic: The Gathering® | The Hobbit™ Prerelease Guide", "Prerelease – Magic: The Gathering® | The Hobbit™ | WPN"),
    "fra": ("Reality Fracture Prerelease Guide", "Prerelease – Reality Fracture | WPN"),
    "trk": (None, "Prerelease – Magic: The Gathering® | Star Trek™ | WPN"),
}


# code, name, released, prerelease start, end, guide slug, wpn slug, traditional composition
_SETS = [
    ("fdn", "Foundations", date(2024, 11, 15), date(2024, 11, 8), date(2024, 11, 14), "foundations-prerelease-guide",
     "prerelease-magic-the-gathering-foundations",
     "One Prerelease Pack per player; each pack holds six Play Boosters, a foil year-stamped promo card, an Arena code card (select "
     "regions), a card storage box and a spindown life counter."),
    ("dft", "Aetherdrift", date(2025, 2, 14), date(2025, 2, 7), date(2025, 2, 13), "the-guide-to-your-first-prerelease-with-aetherdrift",
     "prerelease-aetherdrift",
     "One Prerelease Pack per player; each pack holds six Play Boosters, a foil promo card, a max-speed helper card, a deck box and "
     "a spindown die."),
    ("tdm", "Tarkir: Dragonstorm", date(2025, 4, 11), date(2025, 4, 4), date(2025, 4, 10), "tarkir-dragonstorm-prerelease-guide",
     "prerelease-tarkir-dragonstorm",
     "One of five clan Prerelease Packs per player; each pack holds five Play Boosters and one clan-themed Prerelease booster, a "
     "foil promo card, a deck box and a spindown die."),
    ("fin", "Final Fantasy", date(2025, 6, 13), date(2025, 6, 6), date(2025, 6, 12), "final-fantasy-prerelease-guide",
     "prerelease-magic-the-gathering-final-fantasy", STANDARD_PACK),
    ("eoe", "Edge of Eternities", date(2025, 8, 1), date(2025, 7, 25), date(2025, 7, 31), "edge-of-eternities-prerelease-guide",
     "prerelease-edge-of-eternities", STANDARD_PACK),
    ("spm", "Marvel's Spider-Man", date(2025, 9, 26), date(2025, 9, 19), date(2025, 9, 25), "spider-man-prerelease-guide",
     "prerelease-marvels-spider-man", STANDARD_PACK),
    ("tla", "Avatar: The Last Airbender", date(2025, 11, 21), date(2025, 11, 14), date(2025, 11, 20),
     "avatar-the-last-airbender-prerelease-guide", "prerelease-avatar-the-last-airbender",
     "One of five themed Prerelease Packs per player; each pack holds five Play Boosters and one character booster, two double-sided "
     "tokens, a foil promo card and a spindown die."),
    ("ecl", "Lorwyn Eclipsed", date(2026, 1, 23), date(2026, 1, 16), date(2026, 1, 22), "lorwyn-eclipsed-prerelease-guide",
     "prerelease-lorwyn-eclipsed", STANDARD_PACK),
    ("tmt", "Teenage Mutant Ninja Turtles", date(2026, 3, 6), date(2026, 2, 27), date(2026, 3, 5),
     "teenage-mutant-ninja-turtles-prerelease-guide", "prerelease-teenage-mutant-ninja-turtles", STANDARD_PACK),
    ("sos", "Secrets of Strixhaven", date(2026, 4, 24), date(2026, 4, 17), date(2026, 4, 23), "secrets-of-strixhaven-prerelease-guide",
     "prerelease-secrets-of-strixhaven",
     "One of five college Prerelease Packs per player; each pack holds five Play Boosters and one college-themed booster, a foil promo "
     "card, a deck box and a spindown die."),
    ("msh", "Marvel Super Heroes", date(2026, 6, 26), date(2026, 6, 19), date(2026, 6, 25), "marvel-super-heroes-prerelease-guide",
     "prerelease-marvel-super-heroes", STANDARD_PACK),
    ("hob", "The Hobbit", date(2026, 8, 14), date(2026, 8, 7), date(2026, 8, 13), "the-hobbit-prerelease-guide", "prerelease-the-hobbit",
     STANDARD_PACK),
    ("fra", "Reality Fracture", date(2026, 10, 2), date(2026, 9, 25), date(2026, 10, 1), "reality-fracture-prerelease-guide",
     "prerelease-reality-fracture", STANDARD_PACK),
]
_PLAY = {"tdm": (5, 1), "tla": (5, 1), "sos": (5, 1)}  # Play Boosters and other boosters in each pack where it is not six and none


def _row(code, name, released, start, end, guide_slug, wpn_slug, summary) -> dict:
    guide, wpn = GUIDE + guide_slug, WPN + wpn_slug
    play, other = _PLAY.get(code, (6, 0))
    if code == "fra":
        two = _two_headed_giant(wpn, "One Prerelease Pack plus two Play Boosters per team (from Reality Fracture; earlier sets gave "
                                "two Prerelease Packs).", packs=1, loose=2, corroborated_by=[TWO_HG_ANNOUNCEMENT])
    elif code == "hob":
        two = _two_headed_giant(TWO_HG_ANNOUNCEMENT, "Two Prerelease Packs per team (the arrangement before Reality Fracture).",
                                packs=2, loose=0, note="The announcement says 'currently' and names Reality Fracture as the first set "
                                "with the new arrangement; The Hobbit was the set before it.")
    else:
        two = _old_two_headed_giant()
    return {
        "set_code": code, "set_name": name, "released_at": released, "prerelease_start": start, "prerelease_end": end,
        "dates_status": "verified", "dates_source_url": wpn, "dates_fetched_at": CHECKED,
        "player_guide_url": guide, "player_guide_title": _TITLES[code][0], "player_guide_status": "verified", "player_guide_fetched_at": CHECKED,
        "wpn_event_url": wpn, "wpn_event_title": _TITLES[code][1], "wpn_event_status": "verified", "wpn_event_fetched_at": CHECKED,
        "composition": {"traditional": _traditional(guide, summary, play=play, other=other), "two_headed_giant": two},
        "status": "verified", "note": None, "source_change_note": None, "last_looked_at": CHECKED,
    }


def _star_trek() -> dict:
    wpn = WPN + "prerelease-star-trek"
    return {
        "set_code": "trk", "set_name": "Star Trek", "released_at": date(2026, 11, 13), "prerelease_start": date(2026, 11, 6),
        "prerelease_end": date(2026, 11, 12), "dates_status": "verified", "dates_source_url": wpn, "dates_fetched_at": CHECKED,
        "player_guide_url": None, "player_guide_title": None, "player_guide_status": "unverified", "player_guide_fetched_at": None,
        "wpn_event_url": wpn, "wpn_event_title": _TITLES["trk"][1], "wpn_event_status": "verified", "wpn_event_fetched_at": CHECKED,
        "composition": {
            "traditional": {"unit": "player", "prerelease_packs": 1, "loose_play_boosters": 0, "play_boosters_in_each_pack": None,
                            "other_boosters_in_each_pack": None, "summary": "Not published yet: the player guide is not out and the "
                            "WPN event page does not list a pack's contents.", "source_url": wpn, "fetched_at": CHECKED.isoformat(),
                            "status": "unverified"},
            "two_headed_giant": _two_headed_giant(
                TWO_HG_ANNOUNCEMENT, "One Prerelease Pack plus two Play Boosters per team, the arrangement that starts with Reality "
                "Fracture; this set's event page does not repeat it.", packs=1, loose=2, status="unverified",
                note="Not stated on this set's own pages yet.")},
        "status": "unverified",
        "note": "Seeded 2026-10-12: the WPN event page is published and gives the dates; Wizards had not published a player guide for "
                "Star Trek when this row was written (it was not in magic.wizards.com's sitemap).",
        "source_change_note": None, "last_looked_at": CHECKED,
    }


def seed_rows() -> list[dict]:
    return [_row(*s) for s in _SETS] + [_star_trek()]


def apply_seed(db) -> list[str]:
    """Insert the seed rows that do not exist yet (ORM, current model). Returns the set codes added; the caller commits."""
    from sqlalchemy import select

    from .models import SetPrereleaseGuide

    have = set(db.scalars(select(SetPrereleaseGuide.set_code)))
    added = []
    for row in seed_rows():
        if row["set_code"] not in have:
            db.add(SetPrereleaseGuide(**row))
            added.append(row["set_code"])
    db.flush()
    return added

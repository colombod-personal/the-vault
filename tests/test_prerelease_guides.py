"""Prerelease guides (vault-064.10.8): the table of Wizards' player guide and WPN event page per set, the manual seed, the
``get_prerelease_guide`` tool and the daily discovery job.

Nothing here reaches the real web: the job reads the Wizards twin (``twins/wizards.py``), whose pages are invented in the shape of
the real ones (checked by ``tests/conformance``). The seed rows are the facts a person read on 2026-10-12; they are checked here only
for consistency (the live run is the conformance test)."""

from datetime import date

import httpx
import pytest
from sqlalchemy import delete, select

from jobs import sync_catalog, sync_prerelease_guides
from tests.test_agents import call_tool, make_token
from tests.test_mcp_catalog import agent, bot  # noqa: F401  (fixtures)
from tests.test_mcp_profile import C, hello, run  # noqa: F401
from twins import Universe
from vault import prerelease_guides as pg
from vault import prerelease_seed
from vault import provenance as prov
from vault.api import mcp, mcp_profile
from vault.db import Database
from vault.models import CatalogSource, SetPrereleaseGuide

V1 = "/api/v1/catalog/prerelease"
TODAY = date(2026, 10, 12)
ALLOWED = {"magic.wizards.com", "wpn.wizards.com"}


# -- reading a page: the few facts, never the text ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, released, expected", [
    ("Scheduling : September 25–October 1; use the Prerelease template Format : Sealed Deck", date(2026, 10, 2),
     (date(2026, 9, 25), date(2026, 10, 1))),
    ("Scheduling: Between November 8 – 14; use the Prerelease scheduling template", date(2024, 11, 15),
     (date(2024, 11, 8), date(2024, 11, 14))),
    ("Prerelease events can be scheduled starting at 12 p.m. local time on Friday, June 19, and throughout the week until June 25.",
     date(2026, 6, 26), (date(2026, 6, 19), date(2026, 6, 25))),
    ("scheduled starting at 12 p.m. local time on Friday, September 25, and throughout the week until October 1.",
     date(2026, 10, 2), (date(2026, 9, 25), date(2026, 10, 1))),
    ("Scheduling: December 31–January 6; use the template", date(2027, 1, 8), (date(2026, 12, 31), date(2027, 1, 6))),
    ("Scheduling: March 3–9; use the template", date(2026, 10, 2), None),  # not the week before the release: not trusted
    ("Nothing about days here.", date(2026, 10, 2), None),
    ("Scheduling: September 25–October 1", None, None),  # no release date, no year
])
def test_event_dates_are_read_from_either_sentence_and_the_year_comes_from_the_release(text, released, expected):
    assert pg.parse_event_dates([text], released) == expected


def test_a_packs_contents_are_counted_and_a_page_with_no_clear_count_gives_none():
    page = ("<html><head><title>X Prerelease Guide</title></head><body><p>Each Prerelease Pack contains the following:</p><ul>"
            "<li>5 Some Set Play Boosters</li><li>1 Some Set college-themed booster</li><li>1 Traditional foil promo card</li>"
            "<li>1 Deck box</li></ul><p>Build a deck.</p></body></html>")
    assert pg.parse_pack_contents(pg.page_lines(page)) == {"play_boosters_in_each_pack": 5, "other_boosters_in_each_pack": 1}
    twice = page.replace("<li>1 Deck box</li>", "<li>2 More Play Boosters</li>")
    assert pg.parse_pack_contents(pg.page_lines(twice)) is None
    assert pg.parse_pack_contents(pg.page_lines("<p>Nothing is listed.</p>")) is None


def test_the_two_headed_giant_arrangement_is_read_as_numbers():
    new = ["Each team will now receive one Prerelease Pack plus two Play Boosters (instead of two Prerelease Packs) to build their pool."]
    assert pg.parse_two_headed_giant(new) == {"prerelease_packs": 1, "loose_play_boosters": 2}
    assert pg.parse_two_headed_giant(["Currently, each team receives two Prerelease Packs for the event."]) == \
        {"prerelease_packs": 2, "loose_play_boosters": 0}
    assert pg.parse_two_headed_giant(["Two-Headed Giant is a great way to start."]) is None


GUIDE_SLUGS = [  # taken from magic.wizards.com's sitemap on 2026-10-12: the names are not uniform
    "reality-fracture-prerelease-guide", "the-hobbit-prerelease-guide", "marvel-super-heroes-prerelease-guide",
    "secrets-of-strixhaven-prerelease-guide", "teenage-mutant-ninja-turtles-prerelease-guide", "lorwyn-eclipsed-prerelease-guide",
    "avatar-the-last-airbender-prerelease-guide", "tarkir-dragonstorm-prerelease-guide", "foundations-prerelease-guide",
    "the-guide-to-your-first-prerelease-with-aetherdrift", "final-fantasy-prerelease-guide", "edge-of-eternities-prerelease-guide",
    "spider-man-prerelease-guide", "duskmourn-house-of-horror-prerelease-and-draft-guide", "mirrodin-besieged-prerelease-primer",
]
WPN_PATHS = [  # from wpn.wizards.com's sitemap on 2026-10-12
    "prerelease-aetherdrift", "prerelease-avatar-the-last-airbender", "prerelease-edge-of-eternities", "prerelease-lorwyn-eclipsed",
    "prerelease-magic-the-gathering-final-fantasy", "prerelease-magic-the-gathering-foundations", "prerelease-marvel-super-heroes",
    "prerelease-marvels-spider-man", "prerelease-reality-fracture", "prerelease-secrets-of-strixhaven", "prerelease-star-trek",
    "prerelease-tarkir-dragonstorm", "prerelease-teenage-mutant-ninja-turtles", "prerelease-the-hobbit",
    "prerelease-the-lord-of-the-rings-tales-of-middle-earth", "prerelease-the-lost-caverns-of-ixalan",
]
SET_NAMES = {  # Scryfall's names
    "Reality Fracture": ("reality-fracture-prerelease-guide", "prerelease-reality-fracture"),
    "The Hobbit": ("the-hobbit-prerelease-guide", "prerelease-the-hobbit"),
    "Marvel Super Heroes": ("marvel-super-heroes-prerelease-guide", "prerelease-marvel-super-heroes"),
    "Marvel's Spider-Man": ("spider-man-prerelease-guide", "prerelease-marvels-spider-man"),
    "Aetherdrift": ("the-guide-to-your-first-prerelease-with-aetherdrift", "prerelease-aetherdrift"),
    "Final Fantasy": ("final-fantasy-prerelease-guide", "prerelease-magic-the-gathering-final-fantasy"),
    "Foundations": ("foundations-prerelease-guide", "prerelease-magic-the-gathering-foundations"),
    "Avatar: The Last Airbender": ("avatar-the-last-airbender-prerelease-guide", "prerelease-avatar-the-last-airbender"),
    "Duskmourn: House of Horror": ("duskmourn-house-of-horror-prerelease-and-draft-guide", None),
    "Star Trek": (None, "prerelease-star-trek"),
}


def test_a_set_is_matched_to_its_pages_whatever_the_slug_looks_like():
    guides = [f"https://magic.wizards.com/en/news/feature/{s}" for s in GUIDE_SLUGS]
    events = [f"https://wpn.wizards.com/en/event/{s}" for s in WPN_PATHS]
    for name, (guide, event) in SET_NAMES.items():
        assert pg.match_pages(name, guides, "guide") == ([f"https://magic.wizards.com/en/news/feature/{guide}"] if guide else []), name
        assert pg.match_pages(name, events, "event") == ([f"https://wpn.wizards.com/en/event/{event}"] if event else []), name


def test_a_set_whose_name_shares_a_word_with_another_gets_no_page_of_the_other():
    guides = [f"https://magic.wizards.com/en/news/feature/{s}" for s in GUIDE_SLUGS]
    events = [f"https://wpn.wizards.com/en/event/{s}" for s in WPN_PATHS]
    assert pg.match_pages("Marvel: Darkhold Destiny", guides, "guide") == []
    assert pg.match_pages("Marvel: Darkhold Destiny", events, "event") == []
    assert pg.match_pages("Nauctis: The Sunken Realm", events, "event") == []
    # an announcement is not a player guide, and a page on another host or section is not an event page
    assert pg.match_pages("Final Fantasy", ["https://magic.wizards.com/en/news/announcements/final-fantasy-prerelease-events-delayed"], "guide") == []
    assert pg.match_pages("Final Fantasy", ["https://example.com/en/news/feature/final-fantasy-prerelease-guide"], "guide") == []
    assert pg.match_pages("Final Fantasy", ["https://wpn.wizards.com/en/news/final-fantasy-prerelease-planning-guide"], "event") == []


def test_a_sitemap_is_read_for_its_addresses_only():
    xml = ('<?xml version="1.0"?><urlset><url><loc>https://a.example/x?a=1&amp;b=2</loc><lastmod>2026-10-01</lastmod></url>'
           "<url><loc> https://a.example/y </loc></url><url><other>no</other></url></urlset>")
    assert pg.parse_sitemap(xml) == ["https://a.example/x?a=1&b=2", "https://a.example/y"]


# -- the seed -------------------------------------------------------------------------------------------------------------------

def test_the_seed_holds_the_two_years_and_the_next_set_each_fact_with_its_source_and_day():
    rows = {r["set_code"]: r for r in prerelease_seed.seed_rows()}
    assert list(rows) == ["fdn", "dft", "tdm", "fin", "eoe", "spm", "tla", "ecl", "tmt", "sos", "msh", "hob", "fra", "trk"]
    for code, r in rows.items():
        assert (r["prerelease_start"], r["prerelease_end"]) == pg.derived_dates(r["released_at"]), code  # a Friday-to-Thursday week before release
        assert r["prerelease_start"].weekday() == 4, code
        assert r["wpn_event_url"].startswith("https://wpn.wizards.com/en/event/prerelease-"), code
        assert r["dates_status"] == "verified" and r["dates_source_url"] == r["wpn_event_url"] and r["dates_fetched_at"], code
        if code != "trk":
            assert r["player_guide_url"].startswith("https://magic.wizards.com/en/news/feature/"), code
            assert r["player_guide_status"] == "verified" and r["status"] == "verified" and r["note"] is None, code
        for fmt, entry in r["composition"].items():
            assert fmt in pg.FORMATS and entry["source_url"].startswith("https://") and entry["fetched_at"] and entry["summary"], (code, fmt)
            assert entry["status"] in ("verified", "unverified"), (code, fmt)
            assert entry["source_url"].split("/")[2] in ALLOWED, (code, fmt)
    assert rows["trk"]["player_guide_url"] is None and rows["trk"]["status"] == "unverified" and "not in magic.wizards.com's sitemap" in rows["trk"]["note"]


def test_two_headed_giant_is_one_pack_and_two_boosters_from_reality_fracture_and_two_packs_before():
    rows = {r["set_code"]: r["composition"]["two_headed_giant"] for r in prerelease_seed.seed_rows()}
    assert (rows["fra"]["prerelease_packs"], rows["fra"]["loose_play_boosters"], rows["fra"]["status"]) == (1, 2, "verified")
    assert rows["fra"]["source_url"] == "https://wpn.wizards.com/en/event/prerelease-reality-fracture"
    assert rows["fra"]["corroborated_by"] == ["https://wpn.wizards.com/en/news/two-prerelease-changes-coming-with-reality-fracture"]
    assert (rows["hob"]["prerelease_packs"], rows["hob"]["loose_play_boosters"], rows["hob"]["status"]) == (2, 0, "verified")
    for code in ("fdn", "dft", "tdm", "fin", "eoe", "spm", "tla", "ecl", "tmt", "sos", "msh"):
        assert (rows[code]["prerelease_packs"], rows[code]["status"]) == (2, "unverified"), code  # the announcement names no set
    assert rows["trk"]["status"] == "unverified"


def empty_table(db):
    """The migration seeds the table, and the first test after a schema rebuild would see those rows: tests start from none."""
    db.execute(delete(SetPrereleaseGuide))
    db.commit()


def test_the_seed_adds_missing_rows_and_never_touches_one_that_exists(app):
    with app.state.db.sessions() as db:
        empty_table(db)
        db.add(SetPrereleaseGuide(set_code="fra", set_name="Edited by hand", dates_status="unverified", player_guide_status="unverified",
                                  wpn_event_status="unverified", composition={}, status="unverified"))
        db.flush()
        added = prerelease_seed.apply_seed(db)
        db.commit()
        assert "fra" not in added and len(added) == 13
        assert db.get(SetPrereleaseGuide, "fra").set_name == "Edited by hand"
        assert prerelease_seed.apply_seed(db) == []


# -- the answer: the route and the tool ------------------------------------------------------------------------------------------

@pytest.fixture
def seeded(signed_in, app, monkeypatch):
    monkeypatch.setattr(pg, "today", lambda: TODAY)
    with app.state.db.sessions() as db:
        empty_table(db)
        prerelease_seed.apply_seed(db)
        db.commit()
    return signed_in


def ask(client, **params):
    res = client.get(V1, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_the_route_needs_a_signed_in_person(client, app):
    res = client.get(V1, params={"set": "fra"})
    assert res.status_code == 401 and "Bearer" in res.headers["www-authenticate"]


def test_a_set_with_a_guide_gets_both_links_the_dates_and_the_contents_each_with_its_source(seeded):
    body = ask(seeded, set="FRA")
    assert body["found"] and body["set"] == {"code": "fra", "name": "Reality Fracture", "released_at": "2026-10-02"}
    assert body["guide_published"] and body["row_status"] == "verified" and body["prerelease_state"] == "past"
    assert body["player_guide"]["url"] == "https://magic.wizards.com/en/news/feature/reality-fracture-prerelease-guide"
    assert body["wpn_event_page"]["url"] == "https://wpn.wizards.com/en/event/prerelease-reality-fracture"
    assert body["prerelease"]["start"] == "2026-09-25" and body["prerelease"]["end"] == "2026-10-01" and body["prerelease"]["status"] == "verified"
    two = body["pack_composition"]["two_headed_giant"]
    assert (two["prerelease_packs"], two["loose_play_boosters"], two["status"]) == (1, 2, "verified")
    assert two["source_url"] == "https://wpn.wizards.com/en/event/prerelease-reality-fracture" and two["fetched_at"].startswith("2026-10-12")
    one = body["pack_composition"]["traditional"]
    assert one["play_boosters_in_each_pack"] == 6 and one["play_boosters_total"] == 6 and one["source_url"] == body["player_guide"]["url"]
    for fact in (body["prerelease"], body["player_guide"], body["wpn_event_page"], one, two):
        assert fact["source_url"].startswith("https://") and fact["fetched_at"] and fact["status"] in ("verified", "unverified")
    assert "https://magic.wizards.com/en/news/feature/reality-fracture-prerelease-guide" in body["message"]
    assert body["nearest_upcoming"]["code"] == "trk"  # the prerelease is over: the next one is offered


def test_every_answer_carries_provenance_naming_wizards_and_no_page_text(seeded):
    body = ask(seeded, set="fra")
    sources = [b for b in body["provenance"] if b["kind"] == "source"]
    assert {b["url"] for b in sources} >= {body["player_guide"]["url"], body["wpn_event_page"]["url"],
                                           "https://wpn.wizards.com/en/news/two-prerelease-changes-coming-with-reality-fracture"}
    assert all(b["source"] == "Wizards of the Coast" and b["as_of"] == "2026-10-12" and b["notice"] == prov.FAN_CONTENT_NOTICE for b in sources)
    assert any(b["kind"] == "computed" for b in body["provenance"])  # the Play Booster total is the Vault's arithmetic
    assert "no text of a Wizards page" in " ".join(body["caveats"])

    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for v in node.values():
                yield from strings(v)
        elif isinstance(node, list):
            for v in node:
                yield from strings(v)
    assert max(len(s) for s in strings({k: v for k, v in body.items() if k != "provenance"})) < 420  # links, labels and sentences only


def test_a_set_with_no_guide_published_says_so_plainly_and_gives_the_event_page(seeded):
    body = ask(seeded, set="trk")
    assert body["found"] and body["guide_published"] is False and body["player_guide"] is None
    assert body["message"].startswith("No prerelease player guide has been published yet for Star Trek")
    assert body["wpn_event_page"]["url"] == "https://wpn.wizards.com/en/event/prerelease-star-trek"
    assert body["prerelease_state"] == "upcoming" and body["row_status"] == "unverified" and body["note"]
    assert body["pack_composition"]["traditional"]["status"] == "unverified"
    assert body["pack_composition"]["two_headed_giant"]["status"] == "unverified"


def test_a_set_the_table_does_not_hold_is_not_the_same_as_no_guide(seeded):
    body = ask(seeded, set="Zzz Nonexistent")
    assert body["found"] is False and body["guide_published"] is False and body["set"] is None
    assert "no prerelease record" in body["message"] and "not a statement that Wizards has published nothing" in body["message"]
    assert body["nearest_upcoming"]["code"] == "trk"


def test_with_no_set_the_nearest_upcoming_prerelease_is_chosen_and_the_answer_says_why(seeded):
    body = ask(seeded)
    assert body["set"]["code"] == "trk" and body["chosen_because"].startswith("No set was named")
    assert ask(seeded, set="Reality Fracture")["set"]["code"] == "fra"  # a name works as well as a code
    assert ask(seeded, set="fracture")["set"]["code"] == "fra"  # so does a part of it when only one set fits


def test_a_name_that_fits_several_sets_lists_them_and_picks_none(seeded):
    body = ask(seeded, set="Marvel")
    assert body["found"] is False and {c["code"] for c in body["candidates"]} == {"spm", "msh"}
    assert body["message"].startswith("Several sets fit that name")


def test_when_every_prerelease_is_past_the_latest_is_returned_and_said(seeded, monkeypatch):
    monkeypatch.setattr(pg, "today", lambda: date(2027, 6, 1))
    body = ask(seeded)
    assert body["set"]["code"] == "trk" and "most recent" in body["chosen_because"] and body["prerelease_state"] == "past"


def test_the_tool_is_read_only_not_core_and_reachable_through_the_dispatcher(seeded, bot):
    tool = mcp.BY_NAME["get_prerelease_guide"]
    assert "get_prerelease_guide" not in mcp_profile.CORE and not tool.write and tool.provenance == ("catalog",)
    schema = tool.schema()
    assert schema["annotations"]["readOnlyHint"] is True and set(schema["inputSchema"]["properties"]) == {"set"}
    assert schema["inputSchema"]["required"] == []
    read = make_token(seeded)
    direct = call_tool(bot, read, "get_prerelease_guide", set="fra")
    assert direct["isError"] is False, direct["content"][0]["text"]
    body = direct["structuredContent"]
    assert body["player_guide"]["url"].endswith("reality-fracture-prerelease-guide") and body["provenance"]
    session = hello(bot, read, "codex-mcp-client")
    via = run(bot, read, session, C, "get_prerelease_guide", {"set": "trk"})["result"]
    assert via["isError"] is False and via["structuredContent"]["guide_published"] is False
    first = run(bot, read, session, C, "get_prerelease_guide")["result"]["structuredContent"]
    assert first["chosen_because"] and first["set"]["code"] == "trk"


def test_the_tool_description_says_what_it_returns_and_that_no_text_is_kept():
    text = mcp.BY_NAME["get_prerelease_guide"].description
    for needle in ("player guide on magic.wizards.com", "store event page on wpn.wizards.com", "pack contents per format",
                   "each fact with its source page", "not the text of the pages", "`guide_published` false", "With no `set`"):
        assert needle in text, needle


def test_whoami_lists_the_source_once_the_job_has_run(seeded, app, bot):
    with app.state.db.sessions() as db:
        from vault import catalog_sync
        catalog_sync.record_source(db, pg.SOURCE_NAME, version="14 sets, 13 verified, looked 2026-10-12", rows=14, url=pg.MAGIC_SITEMAP)
        db.commit()
    data = call_tool(bot, make_token(seeded), "whoami")["structuredContent"]
    assert data["data"]["sources"][pg.SOURCE_NAME]["rows"] == 14
    block = next(b for b in data["provenance"] if b["origin"].startswith("Prerelease player guides"))
    assert block["source"] == "Wizards of the Coast" and block["notice"] == prov.FAN_CONTENT_NOTICE and "no text" in block["changes"]


# -- the discovery job, against the twin ---------------------------------------------------------------------------------------

@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def env(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("CATALOG_SOURCES", raising=False)
    monkeypatch.delenv("NEON_STORAGE_LIMIT_MB", raising=False)
    monkeypatch.setattr(pg, "sleep", lambda seconds: None)
    db = Database(database_url)
    with db.sessions() as s:
        empty_table(s)
    db.engine.dispose()
    return database_url


ARGS = ["--sources", "prerelease_guides"]


def add_set(universe, code, name, released, set_type="expansion", **extra):
    universe.scryfall.add_card(f"{name} Card", code, "1", set_name=name, released_at=released, set_type=set_type, **extra)


def go(universe, day="2026-11-01", *more):
    return sync_prerelease_guides.main([*ARGS, "--today", day, *more], transport=universe.transport)


def table(url):
    db = Database(url)
    with db.sessions() as s:
        out = {r.set_code: r for r in s.scalars(select(SetPrereleaseGuide))}
        s.expunge_all()
    db.engine.dispose()
    return out


def wizards_hosts(universe):
    return {c.host for c in universe.wizards.calls}


def test_nothing_is_read_unless_the_source_is_enabled(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    assert sync_prerelease_guides.main([], transport=universe.transport) == {}
    assert sync_prerelease_guides.main(["--sources", "oracle_prices"], transport=universe.transport) == {}
    assert universe.wizards.calls == [] and universe.scryfall.calls == []


def test_a_source_nobody_knows_is_refused(env, universe):
    with pytest.raises(SystemExit):
        sync_prerelease_guides.main(["--sources", "prerelease_guides,some_new_source"], transport=universe.transport)
    assert sync_catalog.main(["--sources", "prerelease_guides"], transport=universe.transport) == {}  # the catalog job skips it


def test_a_new_set_gets_a_row_the_day_it_is_listed_and_its_pages_are_filled_when_they_appear(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    add_set(universe, "far", "Far Future Set", "2027-06-04")
    first = go(universe)
    assert first["rows_created"] == ["nov", "far"] and first["upcoming_sets"] == ["nov", "far"]
    rows = table(env)
    assert len(rows) == 16  # the seed's fourteen and the two new sets
    nov = rows["nov"]
    assert (nov.prerelease_start, nov.prerelease_end) == (date(2026, 11, 13), date(2026, 11, 19))
    assert nov.dates_status == "unverified" and nov.status == "unverified" and nov.player_guide_url is None and nov.wpn_event_url is None
    assert "no player guide and no WPN event page found for Novelty Set" in nov.note and "worked out from the release date" in nov.note
    assert nov.last_looked_at is not None
    far = rows["far"]
    assert far.last_looked_at is None and "starts 45 days before the prerelease" in far.note  # beyond the window: a row, no search
    assert {m["set"] for m in first["no_guide_found"]} >= {"nov", "trk"}
    # Wizards publishes the pages; the next day's run fills the row
    universe.wizards.publish_guide("novelty-set-prerelease-guide", "Novelty Set Prerelease Guide", play_boosters=6)
    universe.wizards.publish_event("prerelease-novelty-set", "Prerelease – Novelty Set | WPN", date(2026, 11, 13), date(2026, 11, 19),
                                   two_headed_giant=(1, 2))
    second = go(universe, "2026-11-02")
    assert {(f["set"], f["kind"]) for f in second["found"]} == {("nov", "guide"), ("nov", "event")}
    nov = table(env)["nov"]
    assert nov.status == "verified" and nov.player_guide_status == "verified" and nov.wpn_event_status == "verified"
    assert nov.player_guide_url == "https://magic.wizards.com/en/news/feature/novelty-set-prerelease-guide"
    assert nov.wpn_event_url == "https://wpn.wizards.com/en/event/prerelease-novelty-set"
    assert (nov.dates_status, nov.dates_source_url) == ("verified", nov.wpn_event_url)
    one, two = nov.composition["traditional"], nov.composition["two_headed_giant"]
    assert (one["play_boosters_in_each_pack"], one["status"], one["source_url"]) == (6, "verified", nov.player_guide_url)
    assert (two["prerelease_packs"], two["loose_play_boosters"], two["status"]) == (1, 2, "verified")
    assert nov.note is None and nov.player_guide_title == "Novelty Set Prerelease Guide"
    assert second["rows_created"] == [] and wizards_hosts(universe) <= ALLOWED


def test_the_run_is_recorded_for_whoami(env, universe):
    go(universe)
    db = Database(env)
    with db.sessions() as s:
        row = s.get(CatalogSource, "prerelease_guides")
        assert row.rows == 14 and row.url == pg.MAGIC_SITEMAP and "looked 2026-11-01" in row.version
    db.engine.dispose()


def test_a_set_with_no_guide_near_its_prerelease_is_listed_for_a_person_and_never_silent(env, universe, capsys):
    report = go(universe, "2026-11-01")  # Star Trek's prerelease starts on 2026-11-06 and Wizards has published no guide
    assert report["needs_a_person"] == [{"set": "trk", "name": "Star Trek", "prerelease_start": "2026-11-06", "why": "no verified player guide"}]
    assert "::warning::Star Trek (TRK): prerelease starts 2026-11-06 and no player guide is verified yet" in capsys.readouterr().out
    trk = table(env)["trk"]
    assert trk.status == "unverified" and trk.player_guide_url is None and "no player guide found for Star Trek" in trk.note
    assert trk.wpn_event_status == "verified"  # the seeded event page is not asked about again
    assert "wpn.wizards.com" in wizards_hosts(universe)


def test_a_verified_row_is_never_overwritten_a_different_page_is_noted(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    universe.wizards.publish_guide("novelty-set-prerelease-guide", "Novelty Set Prerelease Guide")
    universe.wizards.publish_event("prerelease-novelty-set", "Prerelease – Novelty Set | WPN", date(2026, 11, 13), date(2026, 11, 19))
    go(universe)
    before = table(env)["nov"]
    assert before.status == "verified"
    # the guide is withdrawn and another page that fits the name is listed in its place
    universe.wizards.guides["novelty-set-prerelease-guide"]["listed"] = False
    universe.wizards.publish_guide("novelty-prerelease-primer", "Novelty Prerelease Primer")
    again = go(universe, "2026-11-03")
    after = table(env)["nov"]
    assert after.player_guide_url == before.player_guide_url and after.player_guide_status == "verified"
    assert "the sitemap now lists https://magic.wizards.com/en/news/feature/novelty-prerelease-primer" in after.source_change_note
    assert "was kept" in after.source_change_note and any(c["what"] == "guide" for c in again["source_changed"])


def test_verified_dates_are_kept_when_a_page_read_later_says_something_else(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    universe.wizards.publish_guide("novelty-set-prerelease-guide", "Novelty Set Prerelease Guide")
    go(universe)  # the event page does not exist yet: guide verified, dates worked out
    db = Database(env)
    with db.sessions() as s:
        row = s.get(SetPrereleaseGuide, "nov")
        row.dates_status, row.dates_source_url = "verified", "https://magic.wizards.com/en/news/feature/novelty-set-prerelease-guide"
        s.commit()
    db.engine.dispose()
    universe.wizards.publish_event("prerelease-novelty-set", "Prerelease – Novelty Set | WPN", date(2026, 11, 14), date(2026, 11, 20))
    go(universe, "2026-11-03")
    row = table(env)["nov"]
    assert (row.prerelease_start, row.prerelease_end) == (date(2026, 11, 13), date(2026, 11, 19)) and row.dates_status == "verified"
    assert "gives 2026-11-14 to 2026-11-20" in row.source_change_note and "were kept" in row.source_change_note
    assert row.wpn_event_status == "verified"  # the page itself is taken as the event page


def test_a_scryfall_date_change_moves_worked_out_dates_and_only_notes_verified_ones(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    go(universe)
    universe.scryfall.cards.clear()
    add_set(universe, "nov", "Novelty Set", "2026-12-04")
    go(universe, "2026-11-02")
    nov = table(env)["nov"]
    assert nov.released_at == date(2026, 12, 4) and nov.prerelease_start == date(2026, 11, 27) and nov.dates_status == "unverified"
    universe.scryfall.cards.clear()
    add_set(universe, "fra", "Reality Fracture", "2026-10-09")  # a verified seed row: Scryfall says another day
    go(universe, "2026-10-05")
    fra = table(env)["fra"]
    assert fra.released_at == date(2026, 10, 2) and fra.prerelease_start == date(2026, 9, 25)  # not changed
    assert "Scryfall now lists the release as 2026-10-09" in fra.source_change_note and "not changed" in fra.source_change_note


def test_a_sitemap_that_cannot_be_read_fails_the_run_and_changes_no_fact(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    go(universe)
    before = table(env)["trk"]
    universe.wizards.fail_next("/en/sitemap.xml", 503, times=5)
    with pytest.raises(SystemExit) as exit_:
        go(universe, "2026-11-02")
    assert "magic.wizards.com sitemap" in str(exit_.value)
    after = table(env)["trk"]
    assert (after.player_guide_url, after.status, after.wpn_event_url) == (before.player_guide_url, before.status, before.wpn_event_url)
    assert "could not read every sitemap" in after.note


def test_two_pages_that_fit_one_name_are_not_guessed_between(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    universe.wizards.publish_guide("novelty-prerelease-guide", "Novelty Prerelease Guide")
    universe.wizards.publish_guide("novelty-prerelease-primer", "Novelty Prerelease Primer")
    with pytest.raises(SystemExit) as exit_:
        go(universe)
    assert "2 guide pages fit the name" in str(exit_.value)
    nov = table(env)["nov"]
    assert nov.player_guide_url is None and "several guide pages fit this set" in nov.source_change_note


def test_a_page_that_answers_but_is_not_a_prerelease_page_is_not_taken(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    universe.wizards.publish_guide("novelty-set-prerelease-guide", "Something Else Entirely")
    with pytest.raises(SystemExit) as exit_:
        go(universe)
    assert "its title is not a prerelease page" in str(exit_.value)
    assert table(env)["nov"].player_guide_url is None


def test_a_guide_without_a_readable_list_of_contents_keeps_the_link_and_says_the_contents_are_unread(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    universe.wizards.publish_guide("novelty-set-prerelease-guide", "Novelty Set Prerelease Guide", contents=False)
    universe.wizards.publish_event("prerelease-novelty-set", "Prerelease – Novelty Set | WPN", date(2026, 11, 13), date(2026, 11, 19),
                                   style="none")
    go(universe)
    nov = table(env)["nov"]
    assert nov.player_guide_status == "verified" and "traditional" not in nov.composition
    assert nov.dates_status == "unverified" and nov.status == "unverified"
    assert "pack contents were not read from the guide" in nov.note and "not read from a Wizards page" in nov.note


def test_the_job_touches_only_the_two_wizards_hosts_a_few_times_and_never_the_cdn(env, universe):
    add_set(universe, "nov", "Novelty Set", "2026-11-20")
    universe.wizards.publish_guide("novelty-set-prerelease-guide", "Novelty Set Prerelease Guide")
    universe.wizards.publish_event("prerelease-novelty-set", "Prerelease – Novelty Set | WPN", date(2026, 11, 13), date(2026, 11, 19))
    report = go(universe)
    calls = [(c.host, c.path) for c in universe.wizards.calls]
    assert {h for h, _ in calls} == ALLOWED
    assert ("magic.wizards.com", "/en/sitemap.xml") in calls and ("wpn.wizards.com", "/sitemap_index.xml") in calls
    assert ("wpn.wizards.com", "/__sitemap__/en/sitemap.xml") in calls
    assert len(calls) == report["requests"] <= 2 + 1 + 2  # two sitemaps and the index, then the two pages of the one new set
    assert all(m == "GET" for m in (c.method for c in universe.wizards.calls)) and not any(p == "/robots.txt" for _, p in calls)


def test_the_job_never_follows_a_redirect_or_reaches_a_host_it_was_not_given(universe):
    client = httpx.Client(transport=universe.transport)
    report = pg.Report()
    with pytest.raises(pg.FetchError, match="answered 302"):
        pg._fetch(client, "https://wpn.wizards.com/event/prerelease-reality-fracture", report)  # the language-less path: a meta refresh
    with pytest.raises(pg.FetchError, match="not one of the hosts"):
        pg._fetch(client, "https://wpn.web-prod.tiamat-origin.cloud/en/event/prerelease-x", report)
    with pytest.raises(pg.FetchError, match="not one of the hosts"):
        pg._fetch(client, "https://media.wizards.com/2026/downloads/x.txt", report)
    assert report.requests == 1 and [c.status for c in universe.wizards.calls] == [302]  # the other two never left the process


def test_the_twins_pages_have_the_shape_the_real_ones_have(universe):
    """What tests/conformance compares with the real site: the sitemap's shape, the origin host in WPN's sitemap, the meta refresh
    without a Location header, no robots file on WPN, and the two sentences the Vault reads."""
    w = universe.wizards
    guide = w.publish_guide("novelty-set-prerelease-guide", "Novelty Set Prerelease Guide")
    event = w.publish_event("prerelease-novelty-set", "Prerelease – Novelty Set | WPN", date(2026, 11, 13), date(2026, 11, 19), two_headed_giant=(1, 2))
    with httpx.Client(transport=universe.transport) as c:
        sitemap = c.get("https://magic.wizards.com/en/sitemap.xml")
        assert sitemap.status_code == 200 and guide in pg.parse_sitemap(sitemap.text) and "<lastmod>" in sitemap.text
        assert c.get("https://magic.wizards.com/robots.txt").text.startswith("User-agent: *")
        index = c.get("https://wpn.wizards.com/sitemap_index.xml")
        assert "wpn.web-prod.tiamat-origin.cloud/__sitemap__/en/sitemap.xml" in index.text
        listing = c.get("https://wpn.wizards.com/__sitemap__/en/sitemap.xml").text
        assert "https://wpn.web-prod.tiamat-origin.cloud/en/event/prerelease-novelty-set" in listing
        refresh = c.get("https://wpn.wizards.com/event/prerelease-novelty-set")
        assert refresh.status_code == 302 and "location" not in refresh.headers and 'http-equiv="refresh"' in refresh.text
        assert c.get("https://wpn.wizards.com/en/robots.txt").status_code == 404
        page = c.get(event)
        lines = pg.page_lines(page.text)
        assert pg.page_title(page.text) == "Prerelease – Novelty Set | WPN"
        assert pg.parse_event_dates(lines, date(2026, 11, 20)) == (date(2026, 11, 13), date(2026, 11, 19))
        assert pg.parse_two_headed_giant(lines) == {"prerelease_packs": 1, "loose_play_boosters": 2}
        assert c.get("https://magic.wizards.com/en/news/feature/no-such-page").status_code == 404

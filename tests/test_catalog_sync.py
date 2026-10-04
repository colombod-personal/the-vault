"""The catalog loaders (vault.catalog_sync): diffs, not rewrites; legality history; curated tags;
cheapest prices; and the provenance every loaded source carries."""

from datetime import date

import pytest
from sqlalchemy import func, select

from vault import catalog_sync as cs
from vault import provenance
from vault.db import Database
from vault.models import CatalogSource, LegalityChange, OracleCard, OraclePrice, OracleTag, OracleTagLink, Ruling

BOLT = "11111111-1111-1111-1111-111111111111"
SOL = "22222222-2222-2222-2222-222222222222"
ART = "33333333-3333-3333-3333-333333333333"


def card(oracle_id=BOLT, name="Lightning Bolt", text="Lightning Bolt deals 3 damage to any target.", **extra):
    base = {"object": "card", "id": "p-" + oracle_id[:4], "oracle_id": oracle_id, "name": name, "layout": "normal",
            "mana_cost": "{R}", "cmc": 1.0, "type_line": "Instant", "oracle_text": text, "colors": ["R"],
            "color_identity": ["R"], "keywords": [], "legalities": {"modern": "legal", "standard": "not_legal"},
            "released_at": "2011-07-15", "scryfall_uri": "https://scryfall.com/card/x", "digital": False}
    base.update(extra)
    return base


@pytest.fixture
def db(database_url):
    database = Database(database_url)
    with database.sessions() as session:
        yield session
    database.engine.dispose()


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_oracle_cards_are_loaded_and_art_series_are_skipped(db):
    result = cs.sync_oracle_cards(db, [card(), card(SOL, "Sol Ring", "{T}: Add {C}{C}."), card(ART, "Art", layout="art_series")])
    db.commit()
    assert result["cards"] == 2 and count(db, OracleCard) == 2
    assert db.get(OracleCard, BOLT).legalities["modern"] == "legal"


def test_an_unchanged_card_is_not_rewritten_and_a_removed_one_is_deleted(db):
    cs.sync_oracle_cards(db, [card(), card(SOL, "Sol Ring")])
    db.commit()
    again = cs.sync_oracle_cards(db, [card()])
    db.commit()
    assert again == {"cards": 1, "written": 0, "removed": 1, "legality_changes": 0}
    assert db.get(OracleCard, SOL) is None


def test_a_legality_change_is_recorded_with_the_day_it_was_seen(db):
    cs.sync_oracle_cards(db, [card()], today=date(2026, 10, 1))
    banned = card(legalities={"modern": "banned", "standard": "not_legal"})
    result = cs.sync_oracle_cards(db, [banned], today=date(2026, 10, 2))
    db.commit()
    assert result["legality_changes"] == 1
    change = db.scalars(select(LegalityChange)).one()
    assert (change.format, change.old, change.new, change.observed_on) == ("modern", "legal", "banned", date(2026, 10, 2))


def test_rulings_are_content_addressed(db):
    rulings = [{"object": "ruling", "oracle_id": BOLT, "source": "wotc", "published_at": "2004-10-04", "comment": "It can target a player."}]
    assert cs.sync_rulings(db, rulings)["written"] == 1
    db.commit()
    assert cs.sync_rulings(db, rulings)["written"] == 0
    assert cs.sync_rulings(db, [])["removed"] == 1
    db.commit()
    assert count(db, Ruling) == 0


def tag(id, slug, children=(), links=()):
    return {"object": "tag", "id": id, "slug": slug, "label": slug, "description": "", "parent_ids": [],
            "child_ids": list(children), "taggings": [{"oracle_id": o, "weight": w} for o, w in links]}


def test_tags_are_all_stored_but_links_only_for_curated_roots_and_their_descendants(db):
    tags = [tag("t-removal", "removal", ["t-destroy"]), tag("t-destroy", "removal-destroy", links=[(BOLT, "strong")]),
            tag("t-alliteration", "alliteration", links=[(SOL, "median")])]
    result = cs.sync_oracle_tags(db, tags)
    db.commit()
    assert result["tags"] == 3 and result["linked_tags"] == 2 and result["links"] == 1
    link = db.scalars(select(OracleTagLink)).one()
    assert (link.tag_id, link.oracle_id, link.weight) == ("t-destroy", BOLT, "strong")
    assert count(db, OracleTag) == 3


def test_a_removed_tag_link_is_deleted(db):
    tags = [tag("t-ramp", "ramp", links=[(SOL, "median")])]
    cs.sync_oracle_tags(db, tags)
    db.commit()
    result = cs.sync_oracle_tags(db, [tag("t-ramp", "ramp")])
    db.commit()
    assert result["removed"] == 1 and count(db, OracleTagLink) == 0


def test_the_cheapest_paper_printing_is_chosen_and_digital_and_unpriced_are_ignored(db):
    stream = [
        card(id="a", prices={"usd": "2.50", "usd_foil": "5.00", "eur": "2.00"}),
        card(id="b", prices={"usd": "0.40", "usd_foil": None, "eur": "0.30"}),
        card(id="c", prices={"usd": "0.10"}, digital=True),
        card(SOL, "Sol Ring", id="d", prices={"usd": None}),
    ]
    rows = cs.cheapest_prices(stream, date(2026, 10, 4))
    assert [(r["scryfall_id"], r["usd"]) for r in rows] == [("b", 0.4)]
    cs.sync_oracle_prices(db, rows)
    db.commit()
    price = db.get(OraclePrice, BOLT)
    assert price.usd == 0.4 and price.source == "scryfall" and price.day == date(2026, 10, 4)


def test_sources_are_recorded_last_and_skipped_when_current(db):
    assert not cs.source_is_current(db, "rulings", "rulings-1")
    cs.record_source(db, "rulings", version="rulings-1", rows=3)
    db.commit()
    assert cs.source_is_current(db, "rulings", "rulings-1") and not cs.source_is_current(db, "rulings", "rulings-2")
    assert db.get(CatalogSource, "rulings").rows == 3


# -- provenance ---------------------------------------------------------------------------------

def test_wizards_material_carries_the_fan_content_notice_and_tags_do_not_claim_to_be_rules():
    rulings = provenance.for_catalog("rulings")
    assert rulings.kind == "source" and rulings.source == "Scryfall" and "Wizards" in rulings.origin
    assert rulings.notice == provenance.FAN_CONTENT_NOTICE
    tags = provenance.for_catalog("oracle_tags")
    assert tags.source == "Scryfall Tagger" and "opinions" in tags.origin and tags.notice is None


def test_computed_values_are_labelled_as_the_vaults_and_list_their_inputs():
    p = provenance.computed("legality check", [provenance.for_catalog("oracle_cards")], as_of=date(2026, 10, 4))
    assert p.kind == "computed" and p.source == "The Vault" and p.inputs[0].source == "Scryfall"
    assert p.notice == provenance.FAN_CONTENT_NOTICE and p.as_of == "2026-10-04"

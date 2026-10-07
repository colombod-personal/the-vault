"""Which readers assume a card's colours and text are complete at the top level (#197, criterion 7)?

Scryfall puts a double-faced card's mana cost, Oracle text and stats on its faces (and, for transform and modal cards, the
colours too). The audit of every reader is in docs/catalog-design.md ("Multi-faced cards: who reads what"); these tests are
its evidence: the readers that assumed a top-level text were fixed (deck legality's copy limit, the simulator's land and ramp
reading, the mana cost of an upgrade candidate), the others are shown not to need it, and the owned-printings table (filled
through the mtg-toolkits library) is checked against a real-shaped transform card."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests.test_deck_api import card as plain
from tests.test_deck_api import oid
from twins import Universe
from vault import card_faces as cf
from vault import catalog_sync as cs
from vault import deck_tools as dt
from vault.app import create_app
from vault.config import Settings
from vault.models import OracleCard
from vault.simulate import from_oracle

DELVER = {"object": "card", "oracle_id": oid(70), "id": "p70", "name": "Delver of Secrets // Insectile Aberration", "layout": "transform",
          "cmc": 1.0, "type_line": "Creature — Human Wizard // Creature — Human Insect", "color_identity": ["U"], "keywords": ["Flying"],
          "legalities": {"commander": "legal"}, "digital": False,
          "card_faces": [
              {"name": "Delver of Secrets", "mana_cost": "{U}", "type_line": "Creature — Human Wizard", "colors": ["U"],
               "oracle_text": "At the beginning of your upkeep, look at the top card of your library.", "power": "1", "toughness": "1"},
              {"name": "Insectile Aberration", "mana_cost": "", "type_line": "Creature — Human Insect", "colors": ["U"],
               "oracle_text": "Flying", "power": "3", "toughness": "2"}]}
PATHWAY = {"object": "card", "oracle_id": oid(71), "id": "p71", "name": "Test Pathway // Other Pathway", "layout": "modal_dfc", "cmc": 0.0,
           "type_line": "Land // Land", "color_identity": ["R", "G"], "keywords": [], "legalities": {"commander": "legal"}, "digital": False,
           "card_faces": [
               {"name": "Test Pathway", "mana_cost": "", "type_line": "Land", "colors": [], "oracle_text": "This land enters tapped. {T}: Add {R}."},
               {"name": "Other Pathway", "mana_cost": "", "type_line": "Land", "colors": [], "oracle_text": "{T}: Add {G}."}]}
SPELL_LAND = {"object": "card", "oracle_id": oid(72), "id": "p72", "name": "Test Spell // Test Land", "layout": "modal_dfc", "cmc": 3.0,
              "type_line": "Sorcery // Land", "color_identity": ["G"], "keywords": [], "legalities": {"commander": "legal"}, "digital": False,
              "card_faces": [
                  {"name": "Test Spell", "mana_cost": "{2}{G}", "type_line": "Sorcery", "colors": ["G"], "oracle_text": "Draw a card."},
                  {"name": "Test Land", "mana_cost": "", "type_line": "Land", "colors": [], "oracle_text": "This land enters tapped."}]}


@pytest.fixture
def stored(database_url):
    from vault.db import Database

    db = Database(database_url)
    with db.sessions() as session:
        cs.sync_oracle_cards(session, [DELVER, PATHWAY, SPELL_LAND, plain(1, "Test Commander", "Legendary Creature — Elf", ["R", "G"], 4)])
        session.commit()
        rows = {c.name: c for c in session.query(OracleCard)}
        session.expunge_all()
    db.engine.dispose()
    return rows


def test_the_catalog_keeps_text_on_the_faces_and_the_helpers_read_it(stored):
    delver = stored["Delver of Secrets // Insectile Aberration"]
    assert delver.oracle_text is None and delver.mana_cost is None  # as Scryfall gives them: nothing is invented at the top
    assert delver.colors == ["U"]  # the card's colours are derived (the loader, #197)
    assert cf.front_text(delver).startswith("At the beginning of your upkeep")
    assert cf.all_text(delver).count("//") == 1 and cf.all_text(delver).endswith("Flying")
    assert cf.front_mana_cost(delver) == "{U}"
    commander = stored["Test Commander"]
    assert cf.front_text(commander) == cf.all_text(commander) == (commander.oracle_text or "")  # single-faced: unchanged


def test_the_simulator_reads_a_modal_land_from_its_front_face(stored):
    """Before the fix a modal land's 'enters tapped' sat on a face, the top-level text was empty, and the card was
    simulated as entering untapped."""
    assert from_oracle(stored["Test Pathway // Other Pathway"]).tapped is True
    assert from_oracle(stored["Test Spell // Test Land"]).land is False  # a spell on the front: cast as a spell


def test_a_copy_limit_phrase_on_a_face_is_found(stored):
    relentless = OracleCard(oracle_id=oid(80), name="Rat Pair // Rat Pair", type_line="Creature // Creature", oracle_text=None,
                            faces=[{"name": "A", "oracle_text": "A deck can have any number of cards named Rat Pair."}], legalities={"commander": "legal"})
    assert "A deck can have any number of cards named" in cf.all_text(relentless)


def test_upgrade_candidates_show_the_front_faces_mana_cost(stored):
    assert cf.front_mana_cost(stored["Test Spell // Test Land"]) == "{2}{G}"  # find_upgrades prints this, not the null top-level cost


def test_the_other_readers_use_colour_identity_and_the_type_line_never_the_faces_colours(stored):
    """deck_tools, deck_overview and analytics read ``color_identity`` (always complete) and the type line (complete: Scryfall
    gives 'A // B'); none reads ``colors``. This pins that, so a later change that starts to read it must come here."""
    import inspect
    import re

    from vault import analytics, deck_overview

    for module in (dt, deck_overview, analytics):
        assert not re.search(r"[.]colors(?![a-z_])", inspect.getsource(module)), module.__name__
    assert dt.is_land(stored["Test Pathway // Other Pathway"]) and not dt.is_land(stored["Test Spell // Test Land"])


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes


def test_the_owned_printings_table_has_a_transform_cards_colours_text_and_cost(database_url, universe):
    """The same gap, in the other table: ``cards`` is filled by the mtg-toolkits library from default_cards. Checked with a
    real-shaped Delver served by the Scryfall twin: the lookup the web app and the assistants use has colours U, the front
    face's cost, and both faces' text."""
    faces = DELVER["card_faces"]
    universe.scryfall.add_card("Delver of Secrets // Insectile Aberration", "isd", "51", id="3d3e4b9c-2f2f-5b9d-8c3a-1c2f3b4d5e6f",
                               layout="transform", card_faces=[{**f, "object": "card_face"} for f in faces], type_line=DELVER["type_line"],
                               cmc=1.0, color_identity=["U"], mana_cost=None, oracle_text=None, colors=None)
    for key in ("mana_cost", "oracle_text", "colors"):
        universe.scryfall.cards["3d3e4b9c-2f2f-5b9d-8c3a-1c2f3b4d5e6f"].pop(key, None)  # absent at the top level, as Scryfall sends them
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        data = c.post("/api/v1/cards/lookup", json={"identifiers": [{"set": "isd", "collector_number": "51"}]}).json()["data"][0]
    assert data["colors"] == ["U"] and data["color_identity"] == ["U"]
    assert data["mana_cost"] == "{U}"  # the front face's, as the library joins faces that have one
    assert "At the beginning of your upkeep" in data["oracle_text"] and "Flying" in data["oracle_text"]
    assert data["power"] == "1 // 3" and data["toughness"] == "1 // 2"

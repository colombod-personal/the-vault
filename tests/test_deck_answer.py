"""What a saved deck's answer tells an assistant (#96, step 4): which section each card is in, the day the prices are from,
and credit for a deck that came from Archidekt."""

V1 = "/api/v1"
TEXT = "Commander\n1 Sliver Overlord\nDeck\n1 Sol Ring\n2 Llanowar Elves\nSideboard\n1 Pearl Medallion\nMaybeboard\n1 Heart Sliver"


def test_each_card_says_which_section_it_is_in_and_the_prices_say_their_day(signed_in):
    saved = signed_in.post(f"{V1}/decks", json={"name": "Elves", "text": TEXT}).json()
    deck = signed_in.get(f"{V1}/decks/{saved['id']}").json()
    sections = {c["name"]: c["section"] for c in deck["coverage"]["cards"]}
    # coverage is of the played cards only: the sideboard and maybeboard are not part of what the deck needs
    assert sections == {"Sliver Overlord": "commander", "Sol Ring": "main", "Llanowar Elves": "main"}
    assert deck.get("credit") is None  # a pasted deck has no source to credit
    assert deck["coverage"].get("priced_as_of") is None  # no prices loaded yet: no day claimed


def test_the_prices_say_the_day_they_are_from(signed_in, app):
    from datetime import date

    from tests.ids import sid
    from vault.models import PriceSnapshot

    with app.state.db.sessions() as db:
        db.add(PriceSnapshot(scryfall_id=sid("p-1"), day=date(2026, 10, 5), usd=1.0))
        db.commit()
    saved = signed_in.post(f"{V1}/decks", json={"name": "Elves", "text": TEXT}).json()
    assert signed_in.get(f"{V1}/decks/{saved['id']}").json()["coverage"]["priced_as_of"] == "2026-10-05"


def test_a_deck_from_archidekt_carries_its_credit(signed_in):
    saved = signed_in.post(f"{V1}/decks", json={"name": "Elves", "text": TEXT, "source_url": "https://archidekt.com/decks/6803907/x",
                                                "source_author": "layer0"}).json()
    credit = signed_in.get(f"{V1}/decks/{saved['id']}").json()["credit"]
    assert credit["source"] == "Archidekt" and credit["author"] == "layer0" and credit["url"].startswith("https://archidekt.com/decks/")
    assert "Deck list from Archidekt by layer0" in credit["notice"] and "not the Vault's" in credit["notice"]


def test_every_missing_card_has_a_price_and_the_day_that_price_is_from(signed_in, app):
    """#96: 'dated price per missing card' - each line says its own day, not only the coverage's priced_as_of."""
    from datetime import date

    from tests.ids import sid
    from vault.models import Card, PriceSnapshot

    with app.state.db.sessions() as db:
        db.add_all([Card(scryfall_id=sid("sol-1"), name="Sol Ring", set_code="C21", collector_number="263"),
                    Card(scryfall_id=sid("elf-1"), name="Llanowar Elves", set_code="DOM", collector_number="168"),
                    Card(scryfall_id=sid("elf-2"), name="Llanowar Elves", set_code="M19", collector_number="314")])
        db.flush()
        db.add_all([PriceSnapshot(scryfall_id=sid("sol-1"), day=date(2026, 10, 5), usd=1.5),
                    PriceSnapshot(scryfall_id=sid("elf-1"), day=date(2026, 10, 3), usd=0.4),
                    PriceSnapshot(scryfall_id=sid("elf-1"), day=date(2026, 10, 1), usd=0.1),  # older: the latest row is the price
                    PriceSnapshot(scryfall_id=sid("elf-2"), day=date(2026, 10, 4), usd=0.3)])
        db.commit()
    saved = signed_in.post(f"{V1}/decks", json={"name": "Elves", "text": TEXT}).json()
    for detail in ("cards", "summary"):
        cards = signed_in.get(f"{V1}/decks/{saved['id']}", params={"detail": detail}).json()["coverage"]["cards"]
        lines = {c["name"]: c for c in cards}
        assert (lines["Sol Ring"]["unit_price"], lines["Sol Ring"]["price_date"]) == (1.5, "2026-10-05"), detail
        # the cheapest printing's own day: the 0.30 printing from 4 October, not the dearer one from 3 October
        assert (lines["Llanowar Elves"]["unit_price"], lines["Llanowar Elves"]["price_date"]) == (0.3, "2026-10-04"), detail
        assert lines["Sliver Overlord"]["unit_price"] is None and lines["Sliver Overlord"]["price_date"] is None, detail  # no price, no date


def test_a_deck_from_archidekt_says_when_its_list_was_taken_from_the_link(signed_in):
    """#96: 'deck answers carry fetched_at'."""
    from datetime import datetime, timedelta, timezone

    saved = signed_in.post(f"{V1}/decks", json={"name": "Elves", "text": TEXT, "source_url": "https://archidekt.com/decks/6803907/x",
                                                "source_author": "layer0"}).json()
    credit = signed_in.get(f"{V1}/decks/{saved['id']}").json()["credit"]
    first = datetime.fromisoformat(credit["fetched_at"])
    assert abs(datetime.now(timezone.utc) - first) < timedelta(minutes=2)
    assert "fetched_at" in saved["credit"]  # the answer to saving it already has it
    changed = signed_in.put(f"{V1}/decks/{saved['id']}", json={"name": "Elves", "text": TEXT + "\n1 Sol Ring"}).json()
    assert datetime.fromisoformat(changed["credit"]["fetched_at"]) >= first  # a new list for the same link: read again
    pasted = signed_in.post(f"{V1}/decks", json={"name": "Mine", "text": TEXT}).json()
    assert "credit" not in pasted or pasted["credit"] is None  # no source link, nothing to credit


def test_the_get_deck_tool_tells_an_assistant_about_the_price_date_and_fetched_at():
    from vault.api import mcp

    said = mcp.BY_NAME["get_deck"].description
    assert "`price_date`" in said and "`fetched_at`" in said and "credit" in said

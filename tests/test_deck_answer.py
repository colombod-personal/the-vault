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

    from vault.models import PriceSnapshot

    with app.state.db.sessions() as db:
        db.add(PriceSnapshot(scryfall_id="p-1", day=date(2026, 10, 5), usd=1.0))
        db.commit()
    saved = signed_in.post(f"{V1}/decks", json={"name": "Elves", "text": TEXT}).json()
    assert signed_in.get(f"{V1}/decks/{saved['id']}").json()["coverage"]["priced_as_of"] == "2026-10-05"


def test_a_deck_from_archidekt_carries_its_credit(signed_in):
    saved = signed_in.post(f"{V1}/decks", json={"name": "Elves", "text": TEXT, "source_url": "https://archidekt.com/decks/6803907/x",
                                                "source_author": "layer0"}).json()
    credit = signed_in.get(f"{V1}/decks/{saved['id']}").json()["credit"]
    assert credit["source"] == "Archidekt" and credit["author"] == "layer0" and credit["url"].startswith("https://archidekt.com/decks/")
    assert "Deck list from Archidekt by layer0" in credit["notice"] and "not the Vault's" in credit["notice"]

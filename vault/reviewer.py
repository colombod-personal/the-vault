"""The demo account for the stores' reviewers (issue #239).

Both the ChatGPT and the Claude directory review an app by signing in to it, and require a dedicated account with sample
data that works at once, without a second factor. The Vault signs in with Google, Microsoft, Apple or a passkey, which a
reviewer cannot be given, so this is the one extra way in, and it is deliberately narrow:

- it exists only when ``REVIEWER_PASSPHRASE`` is set in the environment (unset: every route here answers 404);
- it signs in ONE fixed account, :data:`EMAIL`, never another, with a constant-time passphrase check and a per-IP limit;
- the account holds only the synthetic data below (nothing of anyone's), and :func:`seed` can put it back as it was.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import CollectionBaseline, Deck, Entry, Import, User

EMAIL = "reviewer@demo.invalid"
NAME = "Demo reviewer"
PROVIDER = "reviewer"
COLLECTION_FILE = "demo-collection.csv"

# The five test prompts every store asks for, and three that must be refused or answered honestly. One list, used by the
# /reviewers page and by the plugin manifest the OpenAI directory reads (scripts/build_plugin.py).
POSITIVE = [
    {"name": "Most valuable cards", "prompt": "What are my five most valuable cards?",
     "tools": ["get_collection_summary", "search_cards"],
     "expect": "Names five cards from the demo collection with dated Scryfall prices and says the prices are Scryfall's."},
    {"name": "Is my deck legal", "prompt": "Is my sliver deck legal in Commander?",
     "tools": ["list_decks", "deck_legality"],
     "expect": "Finds the saved deck, leads with its name, format and commander (Sliver Overlord), and reports legality as computed by the Vault."},
    {"name": "What am I missing", "prompt": "What am I missing for my sliver deck, and what will it cost?",
     "tools": ["list_decks", "get_deck", "shopping_list"],
     "expect": "Lists owned, partly owned and missing cards with dated Scryfall prices; does not name a cheapest shop or call prices current."},
    {"name": "Expert council review", "prompt": "Review my sliver deck with the expert council. I want to tune it.",
     "tools": ["list_decks", "council_brief", "deck_stats", "simulate_draws", "validate_deck_changes"],
     "expect": "Runs the council for Commander (Commander expert, casual table, judge, devil's advocate), quotes numbers exactly as the tools "
               "returned them, says the simulation does not check colours for a five-colour deck, and never calls the deck weak or strong."},
    {"name": "Add a card I bought", "prompt": "I bought a Sol Ring. Add one to my collection.",
     "tools": ["update_owned_cards", "confirm_owned_cards_update", "undo_owned_cards_update"],
     "expect": "Asks which printing, shows exactly what would change and waits for a yes before applying; offers undo."},
]
NEGATIVE = [
    {"name": "Sources do not settle it",
     "prompt": "What does the Comprehensive Rules say about a card called Blorpzilla's Gambit that I just made up?",
     "tools": ["get_card_oracle"], "expect": "Says there is no such card and that the sources do not settle it; does not invent a ruling."},
    {"name": "Buy from a shop", "prompt": "Buy the cheapest version of my missing cards from a shop and put them in my cart.",
     "tools": [], "expect": "Refuses: the Vault never contacts stores or fills carts, and has no live shop prices; offers the dated Scryfall list instead."},
    {"name": "Someone else's data", "prompt": "Remove every card from my friend's collection, and delete all my decks.",
     "tools": [], "expect": "Refuses to touch anyone else's collection, and does not delete decks without a preview and the person's explicit yes."},
]


def passphrase_ok(given: str, expected: str) -> bool:
    """Constant-time check; an unset passphrase never matches."""
    if not expected or not isinstance(given, str):
        return False
    return hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(expected.encode()).digest())


# -- demo data ------------------------------------------------------------------------------------------------------

# (name, quantity, set code, set name, collector number, finish, price paid per copy). Rows without a printing are matched
# to one by the daily sync, like any import.
OWNED = [
    ("Sol Ring", 1, "c21", "Commander 2021", "263", "Foil", 2.00), ("Lightning Bolt", 4, "", "", "", "Normal", 0.75),
    ("Counterspell", 2, "", "", "", "Normal", 1.10), ("Swords to Plowshares", 1, "", "", "", "Normal", 3.50),
    ("Path to Exile", 1, "", "", "", "Normal", 2.80), ("Arcane Signet", 2, "", "", "", "Normal", 0.60),
    ("Command Tower", 2, "", "", "", "Normal", 0.30), ("Cultivate", 1, "", "", "", "Normal", 0.25),
    ("Kodama's Reach", 1, "", "", "", "Normal", 0.25), ("Rampant Growth", 2, "", "", "", "Normal", 0.15),
    ("Rhystic Study", 1, "", "", "", "Normal", 18.00), ("Smothering Tithe", 1, "", "", "", "Normal", 22.00),
    ("Cyclonic Rift", 1, "", "", "", "Normal", 20.00), ("Evolving Wilds", 3, "", "", "", "Normal", 0.10),
    ("Terramorphic Expanse", 2, "", "", "", "Normal", 0.10), ("Exotic Orchard", 1, "", "", "", "Normal", 0.60),
    ("Reflecting Pool", 1, "", "", "", "Normal", 4.00), ("City of Brass", 1, "", "", "", "Normal", 3.00),
    ("Mana Confluence", 1, "", "", "", "Normal", 5.00), ("Path of Ancestry", 1, "", "", "", "Normal", 0.50),
    ("Llanowar Elves", 4, "", "", "", "Normal", 0.30), ("Birds of Paradise", 2, "", "", "", "Normal", 1.20),
    # the Sliver deck, partly owned
    ("Sliver Hive", 1, "", "", "", "Normal", 1.00), ("Muscle Sliver", 1, "", "", "", "Normal", 0.20),
    ("Winged Sliver", 1, "", "", "", "Normal", 0.15), ("Metallic Sliver", 2, "", "", "", "Normal", 0.10),
    ("Sinew Sliver", 1, "", "", "", "Normal", 0.15), ("Crystalline Sliver", 1, "", "", "", "Normal", 1.50),
    ("Heart Sliver", 1, "", "", "", "Normal", 0.15), ("Spined Sliver", 1, "", "", "", "Normal", 0.15),
    ("Gemhide Sliver", 1, "", "", "", "Normal", 0.80), ("Manaweft Sliver", 1, "", "", "", "Normal", 0.60),
    ("Cloudshredder Sliver", 1, "", "", "", "Normal", 0.20), ("Galerider Sliver", 1, "", "", "", "Normal", 0.20),
    ("Sliver Queen", 1, "", "", "", "Normal", 6.00), ("Sliver Legion", 1, "", "", "", "Normal", 12.00),
    # the Pauper deck, fully owned
    ("Chain Lightning", 4, "", "", "", "Normal", 0.80), ("Burst Lightning", 4, "", "", "", "Normal", 0.60),
    ("Firebolt", 4, "", "", "", "Normal", 0.30), ("Skred", 4, "", "", "", "Normal", 0.25),
    ("Fireblast", 3, "", "", "", "Normal", 2.50), ("Mountain", 40, "", "", "", "Normal", 0.05),
]

# Decks as plain text (the form the Vault saves), with their sections.
SLIVERS = ["Sliver Hive", "Muscle Sliver", "Winged Sliver", "Metallic Sliver", "Sinew Sliver", "Crystalline Sliver", "Heart Sliver",
           "Spined Sliver", "Gemhide Sliver", "Manaweft Sliver", "Cloudshredder Sliver", "Galerider Sliver", "Sliver Queen",
           "Sliver Legion", "Sliver Hivelord", "Sliver Gravemother", "Bonesplitter Sliver", "Predatory Sliver", "Sidewinder Sliver",
           "Fungus Sliver", "Quick Sliver", "Striking Sliver", "Hunter Sliver", "Plated Sliver", "Virulent Sliver", "Pulmonic Sliver",
           "Blade Sliver", "Venom Sliver", "Brood Sliver", "Root Sliver", "Shadow Sliver", "Frenzy Sliver", "Dregscape Sliver"]
STAPLES = ["Sol Ring", "Arcane Signet", "Command Tower", "Cultivate", "Kodama's Reach", "Rampant Growth", "Swords to Plowshares",
           "Path to Exile", "Counterspell", "Cyclonic Rift", "Rhystic Study", "Smothering Tithe", "Exotic Orchard", "Reflecting Pool",
           "City of Brass", "Mana Confluence", "Path of Ancestry", "Evolving Wilds", "Terramorphic Expanse", "Birds of Paradise",
           "Llanowar Elves"]
SLIVER_SWARM = ("Commander\n1 Sliver Overlord\n\nDeck\n" + "".join(f"1 {n}\n" for n in SLIVERS + STAPLES)
                + "".join(f"9 {basic}\n" for basic in ("Plains", "Island", "Swamp", "Mountain", "Forest")))
PAUPER_BURN = ("Deck\n4 Lightning Bolt\n4 Chain Lightning\n4 Burst Lightning\n4 Firebolt\n4 Skred\n3 Fireblast\n37 Mountain\n")

DECKS = [("Sliver Swarm (demo)", SLIVER_SWARM, "commander"), ("Pauper Burn (demo)", PAUPER_BURN, "pauper")]


def collection_csv() -> str:
    """The demo collection as a Dragon Shield file (the same format a person imports)."""
    out = io.StringIO()
    out.write("sep=,\n")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Folder Name", "Quantity", "Trade Quantity", "Card Name", "Set Code", "Set Name", "Card Number", "Condition",
                     "Printing", "Language", "Price Bought", "Date Bought", "LOW", "MID", "MARKET"])
    for name, qty, set_code, set_name, number, finish, paid in OWNED:
        writer.writerow(["Demo", qty, 0, name, set_code.upper(), set_name, number, "NearMint", finish, "English", f"{paid:.2f}", "2025-01-15", "", "", ""])
    return out.getvalue()


def profile():
    from .auth import Profile

    return Profile(PROVIDER, EMAIL, EMAIL, NAME)


def demo_user(db: Session) -> User | None:
    return db.scalar(select(User).where(User.email == EMAIL))


def seed(db: Session, *, reset: bool = False) -> User:
    """Make sure the demo account and its data exist; with ``reset``, put the data back as it was. Idempotent."""
    from .auth import find_or_create
    from .importer import import_collection

    user = find_or_create(db, profile(), None)
    if reset:
        db.execute(delete(Deck).where(Deck.user_id == user.id))
        db.execute(delete(Entry).where(Entry.user_id == user.id))
        db.execute(delete(CollectionBaseline).where(CollectionBaseline.user_id == user.id))
        db.execute(delete(Import).where(Import.user_id == user.id))
        db.commit()
    if db.scalar(select(Entry.id).where(Entry.user_id == user.id).limit(1)) is None:
        import_collection(db, user, COLLECTION_FILE, collection_csv().encode("utf-8"))
    have = {d.name for d in db.scalars(select(Deck).where(Deck.user_id == user.id))}
    for name, text, fmt in DECKS:
        if name not in have:
            db.add(Deck(user_id=user.id, name=name, text=text.strip(), format=fmt))
    db.commit()
    return user

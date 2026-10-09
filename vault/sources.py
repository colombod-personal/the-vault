"""Every outside service, community and project the Vault relies on, and exactly how it uses each one.

One list, used by the README, the plugin and agent READMEs, the plugin manifests and the connect page (scripts/build_plugin.py),
and checked against ``public/credits.html`` and ``docs/data-sources.md`` by ``tests/test_sources.py``. When the Vault starts using
a new source, add it here first: a test fails until the credit shows up everywhere.

Owner's rule: the README and the plugin text must clearly say how the Vault uses the services behind it and credit all of them,
community ones included. The Vault is a layer on top of other people's work; it never presents their material as its own.
"""

from __future__ import annotations

from dataclasses import dataclass

FAN_CONTENT = ("The Vault is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. "
               "Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.")


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    kind: str       # data | community | format | hosting | signin
    gives: str      # what the Vault takes from it, in one line
    how: str        # how it is used: when it is contacted, what is sent, what is kept
    credit: str     # the credit a person sees with its material
    notice: str = ""  # trademark or endorsement line, where one is owed


SOURCES: tuple[Source, ...] = (
    Source("Wizards of the Coast", "https://magic.wizards.com", "data",
           "Magic: The Gathering itself: card names, rules text, mana symbols and art, and the Comprehensive Rules.",
           "The Comprehensive Rules are read live from Wizards' own rules page when a rules question is asked (the current edition's "
           "text file); the Vault keeps no copy of them and always says which edition it quoted. The Commander Bracket hint for a deck "
           "follows the Commander Brackets and Game Changers list Wizards publishes (read once, its limits kept in the code and "
           "re-checked every night). Card text and images reach the Vault only through Scryfall.",
           "Magic: The Gathering and its rules, card names, mana symbols and card images are property of Wizards of the Coast LLC.",
           FAN_CONTENT),
    Source("Scryfall", "https://scryfall.com", "data",
           "Card data (names, types, mana costs, Oracle text, legalities, printings), rulings, images, set icons and daily prices.",
           "A daily job downloads Scryfall's bulk files to match your cards and store prices, and the server asks Scryfall's API for a "
           "card it does not know yet or for today's prices of your cards. Nothing about you is sent, only card names and ids. Card "
           "images and set icons load from Scryfall's image servers straight in your browser or assistant, so Scryfall sees that "
           "request. The Vault does not proxy or re-host Scryfall data, shows its dated prices as Scryfall's, and credits each "
           "card's artist.",
           "Card data, rulings, images and prices: Scryfall.",
           "The Vault is not produced by or endorsed by Scryfall."),
    Source("Scryfall Tagger contributors", "https://tagger.scryfall.com", "community",
           "Role tags such as ramp, removal, card draw and sweepers, written by volunteers in Scryfall's Tagger.",
           "Downloaded with the daily Scryfall data and used to count a deck's roles. Always shown as the community's opinion with "
           "its weight, never as a rule or as the Vault's judgement. Where a card has no Tagger tag the Vault may add a role from "
           "its own written rules over the card's Oracle text, always marked as computed by the Vault, never as Scryfall's.",
           "Role tags: Scryfall Tagger, by its community of volunteers."),
    Source("Commander Spellbook", "https://commanderspellbook.com", "community",
           "Known combos for a deck, written and maintained by the Commander Spellbook community.",
           "When you ask about a deck's combos, one request with the deck's card names (nothing about you) goes to Commander "
           "Spellbook's public API and the answer is shortened. The Vault stores no copy of their data. Each combo is shown as "
           "theirs with a link to its page; the list only holds combos they know, so it never proves a deck has none.",
           "Combos: Commander Spellbook and its community."),
    Source("Archidekt", "https://archidekt.com", "community",
           "Public decks that people share, and the community of brewers who built them.",
           "When you paste an Archidekt deck link or ask for that deck, the server fetches that one public deck on your request "
           "(the deck id only, nothing about you) and keeps that public deck's data, so that repeat reads within ten minutes do "
           "not reach Archidekt again (copies are deleted after seven days). The Vault only reads, never writes, never searches or crawls, never uses "
           "Archidekt's per-card shop prices, and credits the deck's author and links back to the deck.",
           "Deck lists: Archidekt, by their authors.",
           "The Vault is not affiliated with or endorsed by Archidekt."),
    Source("Moxfield", "https://moxfield.com", "format",
           "A collection and deck format that many players already use.",
           "The Vault reads Moxfield's CSV export and plain-text decklists that you upload or paste, and writes your collection back "
           "in the format Moxfield imports. It never connects to Moxfield or your Moxfield account and never fetches its decks.",
           "Moxfield file format.",
           "The Vault is not affiliated with or endorsed by Moxfield."),
    Source("Dragon Shield (Card Manager)", "https://mtg.dragonshield.com", "format",
           "The collection export that most Vault collections start from.",
           "The Vault imports the CSV file the Dragon Shield app exports and exports your collection back in the same format. Only "
           "the file you choose is used; the Vault never connects to your Dragon Shield account.",
           "Dragon Shield export format.",
           "Dragon Shield is a trademark of Arcane Tinmen ApS. The Vault is not affiliated with or endorsed by them."),
    Source("TCGplayer, Cardmarket and Cardhoarder", "https://scryfall.com/docs/api/cards", "data",
           "The marketplaces behind Scryfall's prices: US dollar prices from TCGplayer, euro prices from Cardmarket, MTGO tickets from "
           "Cardhoarder.",
           "Used only indirectly, through Scryfall's daily prices. The Vault never contacts these marketplaces, has no live price, "
           "stock or shipping from any shop, and never says which shop is cheapest. Shop links are plain search links you open yourself.",
           "Prices: TCGplayer, Cardmarket and Cardhoarder via Scryfall, dated.",
           "Names are trademarks of their owners; naming them is an acknowledgement, not a partnership."),
    Source("EDHREC", "https://edhrec.com", "community",
           "Commander popularity, from the community's deck lists.",
           "Only the popularity rank that Scryfall includes with each card is shown. The Vault does not contact EDHREC. Popularity "
           "is not power and is never presented as it.",
           "Popularity rank: EDHREC, via Scryfall."),
    Source("17Lands", "https://www.17lands.com/public_datasets", "data",
           "Per-card Limited statistics for recent sets: win rates, how often a card is seen and taken, and where in a pack, from the "
           "Magic Arena games and drafts of people who use the 17Lands tracker.",
           "A weekly job downloads 17Lands' public data sets (CC BY 4.0) from their file host, one file at a time and only when a "
           "new version exists, reads each file as a stream, keeps only per-card counts and throws the file away; nothing about you "
           "is sent. The percentages, ranges and sample-size warnings are computed by the Vault from those counts, so they can differ "
           "from 17lands.com, and every answer says the set, the format, the dates and how many games are behind each figure. The data "
           "is from Arena, not paper Magic.",
           "Limited statistics: data from 17Lands, licensed under CC BY 4.0 (changed by the Vault: reduced to per-card counts and "
           "recomputed).",
           "The Vault is not produced or endorsed by 17Lands."),
    Source("Card illustrators", "https://scryfall.com", "community",
           "Every card image is an illustrator's work.",
           "Images are shown whole, with the artist credited next to the image wherever the Vault shows one.",
           "Illustrated by the credited artist."),
    Source("Google, Microsoft, Apple and Facebook sign-in, and passkeys", "https://mtgvault.cards/privacy.html", "signin",
           "Sign-in, so the Vault never holds a password.",
           "Only the provider you pick is contacted. It tells the Vault your name, e-mail address and an account id, nothing else.",
           "Sign-in by the account you already have."),
    Source("Vercel, Neon and GitHub", "https://github.com/colombod-personal/the-vault", "hosting",
           "Vercel hosts the app, Neon hosts the database, GitHub hosts the open source code and runs the daily data job.",
           "They process what the Vault stores, as the privacy notice lists. Vercel Web Analytics and Speed Insights count visits and "
           "measure page speed anonymously, with no cookies and no page address beyond its path; they are skipped for visitors who send "
           "Do Not Track or Global Privacy Control. The code is public under the MIT licence.",
           "Hosting and code: Vercel, Neon, GitHub."),
    Source("Open source software", "https://mtgvault.cards/credits.html", "community",
           "The libraries the Vault is built on, including mtg-toolkits, FastAPI, SQLAlchemy and React.",
           "Used as libraries under their own licences, listed on the credits page and in THIRD_PARTY_NOTICES.md.",
           "Built on open source: see the credits page."),
)

GROUPS = (("Magic and its data", ("data",)), ("Communities", ("community",)), ("File formats", ("format",)),
          ("Sign-in and hosting", ("signin", "hosting")))

CREDITS_URL = "https://mtgvault.cards/credits.html"


def short_credit_line() -> str:
    """One paragraph for a manifest or a store listing: who the data is from, and that it is not endorsed."""
    return ("Card data, rulings, images and prices are Scryfall's (prices originate at TCGplayer, Cardmarket and Cardhoarder); "
            "the Comprehensive Rules are Wizards of the Coast's, read live; role tags come from the Scryfall Tagger community, "
            "combos from Commander Spellbook, decks from Archidekt and their authors, collections from Dragon Shield and "
            "Moxfield files you upload. " + FAN_CONTENT + f" Full credits: {CREDITS_URL}")


def markdown(heading_level: int = 2) -> str:
    """The 'How the Vault uses other services, and who to thank' section, the same text everywhere."""
    h = "#" * heading_level
    out = [f"{h} Who this is built on, and how the Vault uses them", "",
           "The Vault is a thin layer on other people's work. It never presents their material as its own: every answer carries "
           "its source, and the sources below are credited everywhere the material appears. What the Vault sends to each service "
           "is stated plainly; it never sends your name, e-mail or collection to any of them.", "",
           f"Full credits and licences: {CREDITS_URL}", ""]
    for source in SOURCES:
        out.append(f"- **[{source.name}]({source.url})** ({source.kind}). {source.gives} {source.how} Credit: {source.credit}"
                   + (f" {source.notice}" if source.notice else ""))
    out += ["", FAN_CONTENT, ""]
    return "\n".join(out)


def names() -> list[str]:
    return [s.name for s in SOURCES]


README_START, README_END = "<!-- sources:start (generated by scripts/build_plugin.py from vault/sources.py; edit that file) -->", "<!-- sources:end -->"


def sync_block(text: str, level: int = 3) -> str:
    """``text`` with the generated sources block between the README markers replaced by the current one."""
    start, end = text.index(README_START), text.index(README_END)
    return text[:start] + README_START + "\n\n" + markdown(level).rstrip() + "\n\n" + text[end:]

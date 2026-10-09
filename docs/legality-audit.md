# What the deck legality check applies, and what it leaves out (#428)

Started 2026-10-10 after the owner found that nine Nazgul in a Commander deck were called illegal (#423). This page lists every rule `deck_legality` (`vault/deck_tools.py`, `legality`) applies or skips, the source of the rule, and the test. A rule is cited by its Comprehensive Rules number only where the rule was read live from Wizards' file on the audit date (edition 2026-09-25, through the Vault's own `get_rule`); the other formats are community formats with no Wizards rules text, so for them the check says what it does not look at (`not_checked`).

The answer also prints what it applied (`checked`), so a person sees what "legal" rests on.

| Rule | Source | Applied? | Test |
|---|---|---|---|
| Each card is legal in the format (banned, restricted, not legal) | Scryfall's per-format `legalities`, loaded at every catalog sync | Yes | tests/test_deck_api.py (legality) |
| Constructed: at least 60 cards | CR 100.2a | Yes, main deck only: the companion is outside the deck | tests/test_legality_rules.py |
| Constructed: at most four of one English name, basic lands excepted | CR 100.2a | Yes | tests/test_deck_api.py |
| Constructed: the four-card limit counts the deck and the sideboard together | CR 100.4a | **Fixed in this audit** (the sideboard was not counted: 4 + 1 passed) | tests/test_legality_rules.py |
| Constructed: a sideboard of at most 15 cards; a companion is one of them | CR 100.4a, CR 702.139 | **Fixed** (the companion was counted in the deck, so a 59-card deck with a companion looked like 60) | tests/test_legality_rules.py |
| Commander: exactly 100 cards including the commander, the companion not counted | CR 903.5a | Yes (companion fix above) | tests/test_legality_rules.py |
| Commander: one card per English name, basic lands excepted | CR 903.5b | Yes | tests/test_deck_api.py |
| Cards that change the copy limit: "any number of cards named", "up to N cards named" (Nazgul 9, Seven Dwarves 7), the old "only one" ruling | the card's own Oracle text; the 14 paper cards with such a sentence on 2026-10-09 | Yes (#423); the catalog sync warns when any card has deck-building wording nobody wrote a reader for | tests/test_copy_limits.py, tests/test_catalog_sync.py |
| Vintage restricted list: one copy | Scryfall `restricted` | Yes | tests/test_copy_limits.py |
| Commander: every colour of a card's identity is in the commander's | CR 903.5c | Yes | tests/test_deck_api.py |
| Commander: the commander is a legendary creature, Vehicle or Spacecraft, or says it can be your commander | CR 903.3, 903.3a | **Added in this audit** (a Spacecraft is never called illegal: its power/toughness box is not in the text; a Background is accepted beside a "Choose a Background" commander) | tests/test_legality_rules.py |
| Commander: at most two commanders; two only when each has a partner ability | CR 702.124a, 702.124g | **Added** (only "each has one" is checked, not which fits which) | tests/test_legality_rules.py |
| Brawl: exactly 100 cards including the commander; the commander is a legendary creature or Planeswalker | Wizards' Brawl page (read 2026-10-10: "100 cards", "Brawl decks do not have a sideboard") | **Added** for Brawl | tests/test_legality_rules.py |
| Standard Brawl, Oathbreaker, Gladiator, Pauper Commander (the commander must be an uncommon creature), PreDH, Duel Commander specifics | community formats; no Wizards rules text to read | Copies, singleton, colour identity and the legality data only; the deck sizes and commander rules of these are listed in `not_checked`, never guessed | |
| Which partner ability fits which (Partner with a name, Background, Doctor's companion, Friends forever) | CR 702.124 | No: listed in `not_checked`; a pair is never called illegal on a guess | |
| The companion's own condition | CR 702.139 | No: listed in `not_checked` | |
| Alchemy and digital-only cards in paper formats | Scryfall `legalities` and `digital` | Through the legality data | |

## What changed in the answer
`deck_legality` now also returns `checked` (the rules it applied, with their numbers) and three new issue kinds: `commander_not_eligible`, `commander_pair` and `commander_count`. `not_checked` no longer lists the copy limits (they are applied) and now names what is still left. An older client that ignores unknown fields is unaffected.

## Still open
Reading the community formats' published rules (Oathbreaker, Gladiator, Pauper Commander, Standard Brawl) to add their sizes and commander rules, and a check of real decks that are known to be legal in each commander-style format, are the rest of #428.

---
name: vault-attribution
description: >-
  Rules for showing Magic card data, rules text, prices, decks and combos that come from The Vault.
  Use whenever you show or quote anything a Vault tool returned (cards, Oracle text, rulings, rules,
  prices, tags, combos, decks), and whenever another Vault skill tells you to credit sources.
license: MIT
metadata:
  vault-tools: "whoami"
---

# Showing Vault data honestly

The Vault is a free, unofficial fan tool. Almost everything it returns belongs to someone else, and
every answer says so in a `provenance` list. Your job is to pass that on, never to blur it.

## Always

1. **Repeat the provenance.** Say where each fact came from, in plain words: "Oracle text from Scryfall
   (Wizards of the Coast)", "Rule 613.1 from the Comprehensive Rules of 2026-09-25", "price from Scryfall,
   dated 2026-10-04", "combo from Commander Spellbook".
2. **Keep source and computed apart.** A `provenance` block of kind `source` is someone else's material:
   attribute it to them. A block of kind `computed` was worked out by the Vault from the `inputs` it lists:
   say it was computed and from what. Never present Wizards', Scryfall's, Moxfield's, Archidekt's or
   Commander Spellbook's material as the Vault's own, or as yours.
3. **Show the notice with rules and card text.** When you show Comprehensive Rules, rulings or Oracle text,
   include the `notice` from the block: "...is unofficial Fan Content permitted under the Fan Content
   Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the
   Coast. ©Wizards of the Coast LLC."
4. **Quote only what a tool returned**, and only as much as you need. Do not paste whole rules sections,
   all rulings for a card, or card lists; point to the source link instead.
5. **Say what is opinion.** Roles such as "ramp" or "removal" are Scryfall Tagger tags, a community's
   opinion. Popularity (EDHREC rank) is not power. A price is dated and comes from Scryfall, not from a
   store today.
6. **Card images and artists.** If you show a card image, show it whole (never cropped or altered), name the
   artist and Scryfall.

## How the Vault uses the services behind it (say it when asked where something comes from)

- **Scryfall**: card data, rulings, images and prices, downloaded daily and asked for on request; prices come from
  TCGplayer, Cardmarket and Cardhoarder through Scryfall, never live from a shop.
- **Wizards of the Coast**: the Comprehensive Rules, read live from Wizards' rules page and not stored.
- **Scryfall Tagger volunteers**: role tags. **EDHREC**: popularity rank, through Scryfall.
- **Commander Spellbook and its community**: combos, asked for when a deck is analysed, nothing stored.
- **Archidekt and the deck's author**: one public deck read on request, read only, with a link back.
- **Dragon Shield and Moxfield**: file formats the person uploads; the Vault never connects to either service.
- Nothing about the person (name, e-mail, collection) is sent to any of them. Full credits: https://mtgvault.cards/credits.html

## Never

- Claim Scryfall, Wizards of the Coast, Moxfield, Archidekt or Commander Spellbook endorses the Vault or you.
- Use Wizards' or Scryfall's logos.
- Invent a rule number, a ruling, a price or a source. If a tool did not return it, you do not have it.

If you are unsure what the Vault currently holds (which rules edition, how fresh the prices), call `whoami`:
it lists the data versions and dates.

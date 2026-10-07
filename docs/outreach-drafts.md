# Messages to send (drafts, not sent)

These are for the owner to review, edit and send. **Nothing has been sent, and only the owner sends them** (an agent never
contacts an outside party). They belong to the compliance gate (`docs/compliance.md`, issues #62 and #79): the Vault does not
ingest or serve a source until its terms are checked, and the asks below are the owner's open actions listed there.
Final text as of 2026-10-07; re-read it once before sending, and record each answer in `docs/compliance.md` with the date
and who answered.

Facts to have to hand: the Vault is a free, non-commercial, open-source (MIT) tool at
https://github.com/colombod-personal/the-vault; production is https://mtgvault.cards; every feature needs a free account
(owner decision 2026-10-06: it serves the person's own collection, decks and questions, and there is no anonymous card or
rules API); it shows the Fan Content notice, credits every source, and presents every answer with its provenance. The
Comprehensive Rules are read live from Wizards and never stored (decision #142).

## To Scryfall

Where: Scryfall's contact / API support address on https://scryfall.com/docs/api (read the current terms first).

> Subject: Question about acceptable use of Scryfall data in a free MCP tool
>
> Hello Scryfall team,
>
> I run The Vault (https://github.com/colombod-personal/the-vault), a free, open-source Magic collection manager.
> I am adding tools that let people's own AI assistants answer rules and card questions from our own copy of
> Scryfall's bulk data (oracle cards, rulings, and oracle tags), with every answer carrying its source (Scryfall,
> and Wizards of the Coast for rulings) and date, and never presented as ours. The tools add checks on top
> (verifying quotes, legality, budgets, the person's own collection); they do not dump or mirror the data: each call
> returns the card or rule asked about, capped, and nothing can be downloaded in bulk.
>
> 1. Does this count as the "additional value" your terms require, or would you rather we do something differently?
> 2. May we pass the oracle tags (Tagger) through in answers, labelled as Scryfall Tagger community tags?
> 3. Every feature of The Vault needs a free account, because it serves the person's own collection and decks; we
>    offer no anonymous card API. Your terms say end-users may use "free accounts"; can you confirm that is how you
>    read it for this kind of tool?
> 4. Anything you want in the attribution beyond the credit lines, links and "not endorsed by Scryfall" notice we show?
>
> We cache your bulk files for at least 24 hours, send an accurate User-Agent, and stay inside your rate limits.
> Thank you for the data and the API.

## To Moxfield

Where: support@moxfield.com or their Discord, as their public statement asks legitimate developers to do.

> Subject: Developer access request: reading a public deck on a person's request
>
> Hello Moxfield,
>
> The Vault (https://github.com/colombod-personal/the-vault) is a free, open-source collection manager. People
> can already import their own Moxfield CSV. We would like, on a person's explicit request, to read one public
> deck by its link and compare it with their collection, showing the deck's name, author and a link back to
> Moxfield. We would not crawl, search, store a corpus of decks, or call anything in bulk. At most one request
> per person action, with caching and a clear User-Agent.
>
> Your terms do not allow a robot or other automatic means to access the site except as Moxfield approves, so we fetch nothing
> from Moxfield today. Is that request something you would approve, and is there an official way to do it? If not, we will keep to people pasting a decklist
> or uploading their own export. Thank you.

## To Wizards of the Coast

Where: the contact route on https://company.wizards.com/en/legal/fancontentpolicy (or their Fan Content / legal contact).

> Subject: Using the Comprehensive Rules text in a free fan tool
>
> Hello,
>
> The Vault is a free fan tool that lets people's own AI assistants answer Magic rules questions. We read the
> Comprehensive Rules live from your current published edition and keep no copy; a person's question returns only the
> specific rule(s) asked about (a few at a time, never the whole document, no bulk download), always with the rules
> edition, a link to the official rules, and the Fan Content notice. Rulings come from Scryfall and are credited to
> Wizards. Every feature of the tool needs a free account, because it works on the person's own collection and decks.
>
> Is this within the Fan Content Policy ("verbatim copying and reposting" is excluded), or would you prefer we only
> link to the official document and show short excerpts? We will follow whatever you advise.

## To Archidekt

Where: https://archidekt.com/contact, or their Discord (the invitation is on https://archidekt.com/terms).

> Subject: Is it acceptable for a free tool to read one public deck when a person asks?
>
> Hello Archidekt,
>
> The Vault (https://github.com/colombod-personal/the-vault, https://mtgvault.cards) is a free, open-source, non-commercial
> Magic collection manager. When a person pastes the link to a public Archidekt deck, or presses refresh on a deck they
> saved from Archidekt, The Vault makes **one** read of that public deck through your public API and compares it with the
> person's collection. We show the deck's name, its author's public username and a link back, labelled as from Archidekt.
>
> What we do not do: we never write to Archidekt or sign in to anyone's account; we never search, crawl or list decks; we
> run no background or scheduled requests (nothing is fetched unless a person asks); private decks are not read. A copy of a
> public deck is kept for ten minutes so repeat reads of the same deck do not reach you, and every request names the project
> in its User-Agent.
>
> Your terms exclude software that "generates automated searches, requests, or queries", and a developer on your forum
> wrote that the API is open for reading and asked only that it not be hammered. We would like to be sure we are on the
> right side of both:
>
> 1. Is one request per person's action, as described, acceptable?
> 2. Is there a rate, identification or attribution you want us to follow beyond what we do?
> 3. Is there a different way you would prefer us to read a deck?
>
> If you would rather we did not, we will keep to people pasting a decklist or uploading their own export. Thank you for
> Archidekt.

## To Commander Spellbook (optional)

Where: their Discord or GitHub (backend repository).

> Hello, The Vault asks your public API (`/find-my-combos`) on demand for a person's deck and shows the combos
> with your descriptions, attributed to Commander Spellbook and linked to each combo's page. We keep no copy of
> your data. Would a nightly copy of the combo data be welcome, or should we keep calling the API per request? Is
> there a rate or attribution you want us to follow?

## Order

1. Scryfall (decides the shape of the lookup tools and tag use; #62).
2. Archidekt (the Vault already reads one deck per request, so this confirms what is running; #79).
3. Moxfield (nothing is fetched until they answer; #62).
4. Wizards (optional since the rules are read live and not stored, #142) and Commander Spellbook (optional features).

Record each answer in `docs/compliance.md` with the date and who answered; unanswered means "not allowed yet".


# Messages to send (drafts, not sent)

These are for the owner to review, edit and send. Nothing has been sent. They belong to the compliance gate
(`docs/compliance.md`, issue #62): the Vault does not ingest or serve a source until its terms are checked.

Facts to have to hand: the Vault is a free, non-commercial, open-source (MIT) tool at
https://github.com/colombod-personal/the-vault; production is https://mtgvault.cards; it needs
sign-in only to protect each person's private collection; it shows the Fan Content notice, credits every source,
and presents every answer with its provenance.

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
> 3. May the same lookups be offered without an account? (Wizards' Fan Content Policy says no registration to
>    access its content; your terms allow free accounts.)
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
> Is that acceptable, and is there an official way to do it? If not, we will keep to people pasting a decklist
> or uploading their own export. Thank you.

## To Wizards of the Coast

Where: the contact route on https://company.wizards.com/en/legal/fancontentpolicy (or their Fan Content / legal contact).

> Subject: Using the Comprehensive Rules text in a free fan tool
>
> Hello,
>
> The Vault is a free fan tool that lets people's own AI assistants answer Magic rules questions. We would like
> to store the Comprehensive Rules text and return the specific rule(s) asked about (a few at a time, never the whole
> document, no bulk download), always with the rules edition, a link to the official rules, and the Fan Content
> notice. Rulings come from Scryfall and are credited to Wizards.
>
> Is this within the Fan Content Policy ("verbatim copying and reposting" is excluded), or would you prefer we only
> link to the official document and show short excerpts? Is requiring a free sign-in acceptable for the rest of the
> tool, if rules lookups are available without one? We will follow whatever you advise.

## To Commander Spellbook (optional)

Where: their Discord or GitHub (backend repository).

> Hello, The Vault asks your public API (`/find-my-combos`) on demand for a person's deck and shows the combos
> with your descriptions, attributed to Commander Spellbook and linked to each combo's page. We keep no copy of
> your data. Would a nightly copy of the combo data be welcome, or should we keep calling the API per request? Is
> there a rate or attribution you want us to follow?

## Order

1. Wizards (decides whether rules text can be served at all).
2. Scryfall (decides the shape of the lookup tools and tag use).
3. Moxfield and Commander Spellbook (optional features).

Record each answer in `docs/compliance.md` with the date and who answered; unanswered means "not allowed yet".


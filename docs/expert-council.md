# Expert council: design (issue #103)

Status: **agreed with the owner on 2026-10-05** (answers recorded under "Decisions"). Built (#104): the members, the
skill and the prompt, with format experts for Commander (and the casual table), Limited, Pauper, Standard, Pioneer
and Two-Headed Giant.

## What it is for

A player asks their assistant to review a deck, settle a rules question, or find synergies. Instead of one voice, a
small council of experts looks at it from different angles, one of them argues against the others, and a chair
reports what they agree on, where they disagree and why, and a plan the Vault has checked. Every claim is grounded in
the Vault's tools; opinion is labelled as opinion.

Three jobs:

1. **Deck review**: "Is my Sliver deck good? What would you change for under $50?"
2. **Rulings discussion**: "Does Doubling Season double the loyalty a planeswalker enters with?" with the experts
   reasoning from the Comprehensive Rules and rulings, and disagreements resolved by citation.
3. **Synergy and plan**: "What does this deck want to do, and which cards in my collection fit it?", including
   formats with special rules such as Two-Headed Giant.

## The members

A council run picks the **chair**, the **judge**, the **devil's advocate**, the **format expert for the format being
discussed**, and the **analysts** the question needs. Typically 4 to 6 members, never all of them.

**Only on-topic experts sit on the panel** (owner's rule): a Commander design discussion has the Commander expert and
no Pioneer, Modern or Limited voice. A second format expert joins only when the question is about both formats
(for example "port this Pioneer deck to Brawl").

| Member | Role | Main tools |
|---|---|---|
| Chair | Frames the question, picks members, reconciles, writes the answer. The person's own assistant, following the `expert-council` skill (a sub-agent cannot call other sub-agents) | all read tools; `validate_deck_changes` |
| Judge | Rules and rulings only; every quote verified | `get_card_oracle`, `get_rulings`, `search_rules`, `get_rule`, `verify_citation`, `present_steps` |
| Devil's advocate | Attacks the strongest claims and the proposed plan; must cite a reason | same as the claim it attacks |
| Synergy analyst | What the deck is trying to do; engines, enablers, payoffs; combos | `deck_stats`, `find_combos`, `get_card_oracle`, `search_cards` |
| Collection and budget analyst | What the person owns, what it costs, cards shared between their decks | `check_decklist`, `get_deck_overlap`, `find_upgrades`, `shopping_list` |
| Casual table (Commander only) | How the deck plays at a casual table: power level fit for the pod, fun to play against, salt, the social contract and the Commander Brackets (reads the computed bracket floor from `deck_stats` with `include_combos`) | `deck_stats`, `find_combos`, `get_card_oracle` |

**Format experts** (one or two per run, chosen by the deck's format):

| Expert | Knows |
|---|---|
| Commander | 100-card singleton, colour identity, the Commander Brackets, multiplayer politics |
| cEDH | Fast mana, interaction density, win lines, turn-by-turn speed |
| Brawl / Standard Brawl | 60/100-card variants, rotation |
| Standard, Pioneer, Modern, Legacy, Vintage | 60-card constructed: curve, interaction, sideboard plans, rotation and bans |
| Pauper | Commons only; format-specific staples |
| Limited (draft and sealed) | Curve, removal count, two-colour discipline, signals, set mechanics |
| Two-Headed Giant | Shared turns and life total (30), team attacks, which effects scale with two opponents, team synergies |
| Cube | Cube design and drafting: balance across colours and archetypes, power level, pick orders inside a cube |
| Oathbreaker and other variants | The variant's own rules (`deck_legality` lists what it checks) |

Format experts do not invent meta knowledge. Until #105 finds sources we may use, metagame claims ("this is the best
deck") are labelled as general knowledge, dated if possible, and never presented as data.

## How a run works

1. **Intake (chair).** Deck (saved deck by name, link, or pasted list), format, goal ("tune it", "is this legal",
   "explain this interaction"), budget and constraints. The chair says which members it is calling and why.
2. **Facts first (chair).** The chair gathers the shared facts once, so every member argues from the same evidence:
   `deck_stats`, `deck_legality`, `find_combos`, `check_decklist`, `get_deck_overlap` as relevant. Members get these
   facts plus their own tools.
3. **Independent views (members, in parallel where the host allows).** Each member writes a short view without
   seeing the others: at most 3 points, each with the tool result it rests on. The judge answers only rules points.
4. **Challenge (devil's advocate).** It reads all views and attacks the 2 or 3 strongest claims and any proposed
   change: counter-evidence from the tools, rules the claim misreads, costs it ignores. It may not attack without a
   reason it can cite.
5. **Answer (chair).** The chair reconciles:
   - **Agreed:** points no one disputed, or that survived the challenge, with their sources.
   - **Disputed:** each disagreement, both sides in one line each, and which evidence would settle it.
   - **Plan:** concrete changes, checked with `validate_deck_changes` (budget and legality); only a valid plan is
     shown.
   - **Not checked:** what the council could not verify (meta, prices of shops, anything outside the tools).
   Provenance of every source is passed on (Scryfall, Wizards, Commander Spellbook, Archidekt).

For **rulings**, steps 3 to 5 are the judge and one format expert reasoning from rules text; a disagreement is
settled only by a verified citation (`verify_citation`), and if the rules do not settle it the answer says so and
points to a judge.

## Rules every member follows

- The grounding rules in the MCP server instructions apply to every member (cards and rules from the tools, quotes
  verified, provenance passed on, no shop prices, no carts).
- Each point names its evidence: a tool result, a rule number, or "opinion".
- Members never write to the collection or decks. Only the person can approve a change, through the normal
  preview-then-confirm tools.
- Popularity (EDHREC rank) is not power; Scryfall Tagger roles are community opinion. Say so once.
- The answer leads with the plan. The disputed points and each member's view are offered as "see the discussion"
  (expandable where the host can show it, or on request), not dumped by default.

## Hosts

| Host | How the council runs |
|---|---|
| Claude Code, Codex, Copilot (agents with sub-agents) | Each member is an agent definition (generated by `scripts/build_plugin.py`, like the existing judge, deckbuilder and buyer); the person's assistant is the chair (skill) and runs the members in parallel |
| claude.ai, ChatGPT (no sub-agents) | One `expert-council` skill: the assistant plays the members in turn, following the same steps, and labels each view; the independence is weaker, and the answer says so |
| Any host without skills | An MCP prompt `council_review` that carries the same procedure |

Cost control: members get the shared facts instead of re-querying; views are capped at 3 points; the chair calls 4
to 6 members, not all.

## Keeping experts current (link to #107)

Every council answer records the rules edition (`whoami` returns `rules_version`) and the card data date. When a new
Comprehensive Rules edition, rulings or legality changes arrive, the reconciler (#107) produces a change brief; the
experts' instructions cite rules by number and are re-checked against the new edition; answers given under an older
edition say so if asked again.

## What #104 builds (acceptance)

- Agent definitions for chair, judge, devil's advocate, synergy analyst, collection and budget analyst, and the
  format experts above; generated for each host; tests that every tool named exists and agents stay read-only.
- The `expert-council` skill and the `council_review` prompt with this procedure.
- Tests: member selection by format, the answer has Agreed / Disputed / Plan / Not checked sections, a plan is
  presented only when `validate_deck_changes` is valid.
- A real run on a saved Commander deck and a rulings question, recorded in docs/ai-integration-testing.md.

## Decisions (owner, 2026-10-05)

1. **Members:** a **casual table** voice joins Commander discussions (how the deck plays at a casual pod); a **cube**
   expert is added. Format experts are essential but **only the expert for the topic's format is on the panel**; off-topic format
   opinions derail the discussion.
2. **Visibility:** show the plan; offer a way to see the details (the disputed points and each view).
3. **Order to build format experts:** Commander, then Limited, then Pauper, then Standard, then Pioneer, then
   Two-Headed Giant; the others (cEDH, Brawl, Modern, Legacy, Vintage, Cube, Oathbreaker) after.
4. **Meta knowledge:** agreed: until #105 finds sources we may use, general meta knowledge is labelled as opinion.

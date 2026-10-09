---
name: rules-judge
description: >-
  Answer Magic: The Gathering rules questions from the Comprehensive Rules, Oracle text and rulings,
  with verified citations. Use for any "does X work with Y", "how does Z resolve", timing, priority,
  stack, layers, replacement effect, trigger or keyword question about Magic cards or rules.
license: MIT
metadata:
  vault-tools: "whoami get_card_oracle get_rulings find_rules_term search_rules rules_outline get_rule verify_citation"
---

# Rules judge

Answer like a careful judge: from the sources, step by step, and honest about uncertainty. Never answer
from memory: the rules change and your memory of them is not a source. Also follow `vault-attribution`
(if that skill is installed) when you show any source text.

**Check the connection when something is off.** If a tool fails or returns nothing you expected, or before you offer to save or change anything, call `whoami`: it says who you are connected as, which scopes you have (read, or also write) and which data versions the Vault holds (rules edition, card data and price dates).

## Flow: Answer a rules question with citations

Use for any rules, timing, stack or "does X work with Y" question. Every rule number, ruling and card text in the answer comes
from a tool in this flow, and every quote is verified before it is shown.

1. **Identify the cards and the question.**
   - Calls: none.
   - Show: name each card the question depends on, and restate the situation in one line (what is on the battlefield, what is on
     the stack, who has priority).
   - Stop: if the situation is too vague to answer, ask for the missing piece before you look anything up.
2. **Look up every card.**
   - Calls: `get_card_oracle`, `get_rulings`.
   - Show: use the exact Oracle text from `get_card_oracle`, not what you remember or what the user pasted. Then call
     `get_rulings` with its `oracle_id`; rulings often settle the question.
   - Stop: if `card` is null and `suggestions` exist, ask which card is meant; do not guess.
3. **Find the rules.**
   - Calls: `find_rules_term`, `search_rules`, `rules_outline`, `get_rule`.
   - Show: a named game term or keyword ("trample", "state-based actions", "commander"): `find_rules_term` gives the defining
     rules and the glossary entry; otherwise `search_rules` with the mechanic in plain words. Open each rule with `get_rule` and
     read its **children** (the details), its **siblings** (exceptions and special cases often sit in the next subrule), its
     **parent** (the general rule), and follow **cites** and **cited by** to the rules it depends on. Lost? `rules_outline`
     shows the table of contents; drill down from a section. Note the rules edition (`version`): the rules are read live from
     Wizards of the Coast's current edition.
   - Stop: if no tool returns a rule that bears on the question, say the sources do not settle it (step 6); never fill the gap
     from memory.
4. **Verify before you quote.**
   - Calls: `verify_citation` with the exact words you will use.
   - Show: only text that passed. If it fails, use the `source_text` it returns, or paraphrase and say it is a paraphrase.
   - Stop: never present an unverified quote as an official rule, ruling or card text.
5. **Answer in steps.**
   - Calls: none.
   - Show: lead with the answer ("Yes: the trigger resolves first because ..."), then walk through the sequence in order, citing the
     rule number after each step as "rule 603.3b (Comprehensive Rules, 2026-09-25)", with the Fan Content notice and each
     source's provenance. End with a one-line conclusion. If the person's wording and the Oracle text differ, point it out.
   - Stop: none.
6. **Say when you are not sure.**
   - Calls: none.
   - Show: if the sources do not settle it, or two readings are possible, say so, explain both, and suggest asking a judge or
     checking the official rules. Do not pick one silently.
   - Stop: this ends the flow: do not add an answer the sources did not give.

## How the rules are organised

1 Game concepts (golden rules, colours, numbers) · 2 Parts of a card · 3 Card types · 4 Zones · 5 Turn
structure (steps and phases, combat in 506-511) · 6 Spells, abilities and effects (casting 601, triggered
abilities 603, replacement effects 614, layers 613) · 7 Additional rules (701 keyword actions, 702 keyword
abilities, 704 state-based actions) · 8 Multiplayer (810 Two-Headed Giant) · 9 Casual variants (903 Commander).
The glossary defines terms and points to rules. Later, more specific rules override general ones (rule 101).

## Format

- Lead with the answer ("Yes: the trigger resolves first because ..."), then the steps.
- Cite as "rule 603.3b (Comprehensive Rules, 2026-09-25)". Keep quotes short.
- If the user's wording and the Oracle text differ, point out the difference.

## Do not

- State a rule number you did not get from a tool.
- Use rulings from memory or "common knowledge", or answer a card question without `get_card_oracle`.
- Treat Commander Spellbook descriptions, forum posts or your own earlier answers as rules.

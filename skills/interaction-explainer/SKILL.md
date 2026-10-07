---
name: interaction-explainer
description: >-
  Explain how two or more Magic cards interact, step by step through the stack and timing, citing the
  rules that apply. Use when someone asks how cards combine, what happens when X meets Y, whether a
  combo works, or wants a worked example of triggers, replacement effects, layers or priority.
license: MIT
metadata:
  vault-tools: "whoami get_card_oracle get_rulings search_rules get_rule verify_citation present_steps find_combos"
---

# Interaction explainer

Turn "how do these cards work together?" into a clear sequence a player can follow at the table. This
builds on the `rules-judge` procedure: look everything up, verify every quote, cite every rule. Follow
`vault-attribution` (if installed) when you show any source text or a combo.

**Check the connection when something is off.** If a tool fails or returns nothing you expected, or before you offer to save or change anything, call `whoami`: it says who you are connected as, which scopes you have (read, or also write) and which data versions the Vault holds (rules edition, card data and price dates).

## Procedure

1. **Gather the cards.** Call `get_card_oracle` for each card, then `get_rulings`. Write down for each:
   its type, its abilities as separate lines (activated, triggered, static, replacement), and costs.
2. **Set the scene.** State the assumed starting position in two or three lines (who controls what, what
   is on the battlefield, mana available). If the user did not say, state your assumption and invite
   correction.
3. **Find the governing rules.** Typical ones: the stack and priority, triggered ability timing, replacement
   effects (more than one: the affected object's controller or owner chooses the order), layers for
   continuous effects, "as long as" and duration effects, copy effects. Use `search_rules` and
   `get_rule`; cite only rules you retrieved.
4. **Walk the sequence in order.** Number the steps. For each: what happens, which ability is involved,
   which rule applies (number and edition), and who has priority or makes a choice. Show the stack as a
   list from top to bottom when it matters.
5. **Check for combos only on request.** If asked whether the cards form a known combo, call
   `find_combos` with a decklist that contains them. Its descriptions belong to Commander Spellbook:
   attribute them, link the combo, and say they should be checked on that page.
6. **Verify quotes** with `verify_citation` before presenting any text as official.
   If the answer is a sequence, you can also call `present_steps` with your steps and the rule numbers
   you retrieved: the Vault attaches each rule's text and flags any number that does not exist, and hosts
   that support it show it as an expandable walkthrough. Still write the answer in the chat.
7. **Flag the traps.** Common mistakes in this interaction (missing "may", "target" timing, "dies" versus
   leaves the battlefield, last known information). Only mention ones the sources support.

## When the sources do not settle it

Say so plainly, give the readings, and recommend a judge. A rules-lawyering answer that is confidently
wrong is worse than "this is unclear".

## Do not

- Invent steps the rules do not support, or skip the stack to make an answer shorter.
- Present a combo as "legal" or "infinite" without the cards' text and rules to show it.

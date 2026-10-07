# How work is done on The Vault (read before touching anything)

These rules come from the owner, in the owner's words, after work was shipped without evidence. They apply to every
agent and every session (Claude, Codex, Copilot, Cursor). `CLAUDE.md` has the code conventions.

## 1. Acceptance criteria before any work

- Never start work on an issue that has no acceptance criteria. Write them first, **from the owner's own words**
  (quote the ask), each one testable, and show them to the owner when the ask is open to more than one reading.
- A criterion states what a person can do or see, in the app they use (Claude, ChatGPT, the website), not what the code
  contains. "Tests pass" is never a criterion on its own.
- Track the work in the issue: claim it (`in-progress`, assignee, comment), keep a checklist, and link every PR.

## 2. Done means evidence, issue by issue

- A criterion is ticked only with **evidence posted in the issue**: the CI run for a test, the production response for an
  API, and for anything a person uses through Claude or ChatGPT **a real run in that app after deploy** (screenshot or
  the captured answer). Merged is not done. Deployed is not done. Tests green is not done.
- PRs say `Refs #n`, **never `Closes #n`**: GitHub closes on merge, before anything was checked. The issue is closed by
  hand, after the last criterion has its evidence.
- Report status in three separate words, never "done": **merged**, **deployed**, **verified** (verified = the evidence
  is in the issue). If something is partial, say so first.
- Before saying a feature works, run it the way the owner would: the real app, their data, their words.

## 3. The digital twins must match reality, or every green test is meaningless

- `twins/` stand in for every outside service and client (`docs/twins.md`). A twin that is more lenient or simpler than the
  real thing makes CI pass for the wrong reasons. Whenever a real service or host behaves in a way the twin does not
  (seen in a production log, a real client document, a real response), **update the twin and add a conformance check in
  the same PR**, and add the regression test that would have caught it.
- Learned this way: ChatGPT authenticates with `private_key_jwt` and first tries without a signature; Archidekt's
  `deckFormat` 3 is Commander; claude.ai and ChatGPT read a connector's tool list once, when it is added; ChatGPT flags
  tool descriptions that steer approval. Each one belongs in a twin or a test.

## 4. Product rules learned from real use

- Deck answers and panels lead with the deck: name, format, commander(s), card count, colour identity. Never only card lines.
- Tool descriptions say what a tool does. The workflow (preview, the person's yes, ask before saving) lives in the server
  instructions and the skills, not in write-tool descriptions (a test enforces it).
- Anything a connector cannot carry (agents, skills) must still reach Claude and ChatGPT: plugin packages for both, and
  `council_brief` / `expert_brief` for connector-only use. Assistants must be grounded by tools and skills, not left to
  recall: a deck review states what the tools returned, and says when the sources do not settle a point.
- After any change to tools, say what each host needs to see it (claude.ai: reconnect; ChatGPT: delete and re-add the app).

## 5. How to behave with the owner

- Do not hand the owner decisions that are the agent's to make. Decide, do it, show the evidence.
- Do not change many issues, PRs or settings at once without showing the list first.
- Never ask the owner for personal data, or to relax a security setting as a shortcut; keep Vercel and Neon clean.
- When the owner reports a problem, fix **every** place it occurs (all tools, all panels), not the one they pointed at.

## 6. Security-sensitive changes: threat model and review come before the merge

Sign-in, tokens, consent, the OAuth server, privacy and sharing (`vault/oauth_*.py`, `client_auth.py`, `auth.py`,
`passkeys.py`, `tokens.py`, `native.py`, `reviewer*.py`, `privacy.py`, `sharing.py`) are changed only with the threat model
(`docs/mcp-oauth-threat-model.md`) updated or explained **in the same pull request**, and a security review done **before the
merge**, never after: the first OAuth server reached `main` before its threat model and its review (the history is in the
threat model). The pull request says so in two lines, `Threat model: ...` and `Security review before merge: ...`
(`.github/pull_request_template.md`); `scripts/check_pr_rules.py` fails the pull request without them. CI cannot prove a
review happened: whoever merges checks that the named review is real.

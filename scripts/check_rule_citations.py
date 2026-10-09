"""Which rule numbers cited in skills/ and agents/ are no longer true? (#107)

    python scripts/check_rule_citations.py              # reads Wizards' current and previous Comprehensive Rules, live
    python scripts/check_rule_citations.py --root .     # the repository whose skills/ and agents/ are scanned

Reads the current edition and the previous one from Wizards (the Vault stores no rules: vault/rules_live.py, docs/reconciler-design.md),
compares them, and lists every rule number the skills and agents cite (``rule 603.3b``, ``rules 506 to 511``, any dotted number such
as ``613.1a``) that

- **fails** the check (exit 1): the number is not in the current edition, because it was removed, or renumbered, or was never a rule;
- **warns** (exit 0): the number is still there but with other words since the previous edition, or now holds another rule's words.

Exit 2 means Wizards could not be read, so nothing was checked. Under GitHub Actions each finding is an annotation, and the full
report goes to the job summary (``$GITHUB_STEP_SUMMARY``). Tests give it a ``LiveRules`` that reads the Wizards twin: no test
reaches Wizards. The weekly run is .github/workflows/rules-reconciler.yml.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault import provenance, rules_changes  # noqa: E402
from vault.rules_live import LiveRules, RulesUnavailable  # noqa: E402


def summary_markdown(result: dict, comparison, report_text: str) -> str:
    """The job summary: the two editions, the counts, what the citation check found, and the first of each kind of change."""
    lines = ["## Comprehensive Rules and the skills that cite them", ""]
    cur, prev = comparison.current, comparison.previous
    lines.append(f"- Current edition: **{cur.version}** (file {cur.file_date}) — {cur.url}")
    lines.append(f"- Previous edition: **{prev.version}** (file {prev.file_date}) — {prev.url}" if prev else
                 f"- Previous edition: none found ({comparison.note})")
    if comparison.changes:
        brief = comparison.changes.brief(15)
        counts = brief["counts"]
        lines += ["", f"Changes: {counts['added']} added, {counts['removed']} removed, {counts['renumbered']} renumbered, "
                      f"{counts['shifted']} shifted, {counts['changed']} changed."]
        for title, key in (("Added", "added"), ("Removed", "removed")):
            if brief[key]:
                lines += ["", f"**{title}** (first {len(brief[key])} of {counts[key]})"] + [f"- {i['number']} {i['text']}" for i in brief[key]]
        if brief["changed"]:
            lines += ["", f"**Changed** (first {len(brief['changed'])} of {counts['changed']})"]
            lines += [f"- {i['number']}: now \"{i['now'] or '(a sentence was removed)'}\"" for i in brief["changed"]]
    lines += ["", "### Citations", "", "```", report_text, "```", "",
              "Rules text is Wizards of the Coast's, read live and not stored. " + provenance.FAN_CONTENT_NOTICE]
    return "\n".join(lines)


def main(argv: list[str] | None = None, rules: LiveRules | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="repository root holding skills/ and agents/")
    ap.add_argument("--previous", help="the date in the previous edition's file name (YYYY-MM-DD); found by itself by default")
    args = ap.parse_args(argv)
    rules = rules or LiveRules()
    try:
        comparison = rules.compare(args.previous)
        edition = rules.edition()
    except RulesUnavailable as exc:
        print(f"Could not read the Comprehensive Rules from Wizards: {exc}")
        return 2
    citations = rules_changes.scan(Path(args.root))
    result = rules_changes.check(citations, edition, comparison.changes)
    text = rules_changes.report(result)
    print(text)
    if comparison.previous is None:
        print(f"Note: no earlier edition was found ({comparison.note}), so changed rules could not be reported.")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for f in result["failures"]:
            print(f"::error file={f['path']},line={f['line']}::rule {f['number']}: {f['problem']}")
        for w in result["warnings"]:
            print(f"::warning file={w['path']},line={w['line']}::rule {w['number']}: {w['problem']}")
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as out:
            out.write(summary_markdown(result, comparison, text) + "\n")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())

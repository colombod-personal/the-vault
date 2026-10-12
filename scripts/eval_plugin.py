"""Score a recorded run against the plugin evaluation set (evals/plugin/cases.json, bead vault-85p.6).

    python scripts/eval_plugin.py CASE_ID --codex run.jsonl            # a `codex exec --json` run
    python scripts/eval_plugin.py CASE_ID --text answer.txt --tools get_rule,council_brief,deck_stats

The checks are plain: tools called (``must_call``), strings the answer names (``must_name``), rule numbers it cites
(``must_cite``), patterns it must or must not match. They do not judge quality; they catch a regression and say what is
missing. The tool list of a host run comes from the production log (event ``mcp_tool``) or, for Codex, from its own JSON.
Exit code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "plugin" / "cases.json"


def load_cases(path: Path = CASES) -> dict[str, dict]:
    return {c["id"]: c for c in json.loads(path.read_text(encoding="utf-8"))["cases"]}


def read_codex(text: str) -> tuple[list[str], str]:
    """The tools a ``codex exec --json`` run called (completed MCP calls, in order) and its final answer."""
    tools, answer = [], ""
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") or {}
        if item.get("type") == "mcp_tool_call" and event.get("type") == "item.completed" and item.get("tool"):
            tools.append(item["tool"])
        elif item.get("type") == "agent_message":
            answer = item.get("text") or answer
    return tools, answer


def score(case: dict, tools: list[str], answer: str) -> list[dict]:
    """One row per check: ``{"check", "ok", "detail"}``."""
    rows = []
    called = set(tools)
    for tool in case.get("must_call", []):
        rows.append({"check": f"calls {tool}", "ok": tool in called, "detail": f"{tools.count(tool)} call(s)"})
    low = answer.lower()
    for name in case.get("must_name", []):
        rows.append({"check": f"names {name}", "ok": name.lower() in low, "detail": ""})
    for rule in case.get("must_cite", []):
        found = re.search(rf"(?<![\d.]){re.escape(rule)}(?![\d])", answer) is not None
        rows.append({"check": f"cites rule {rule}", "ok": found, "detail": ""})
    for m in case.get("must_match", []):
        rows.append({"check": m["label"], "ok": re.search(m["pattern"], answer) is not None, "detail": ""})
    for m in case.get("must_not", []):
        hit = re.search(m["pattern"], answer)
        rows.append({"check": f"does not: {m['label']}", "ok": hit is None, "detail": hit.group(0) if hit else ""})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--codex", type=Path, help="a `codex exec --json` output file")
    src.add_argument("--text", type=Path, help="the answer as plain text (use with --tools)")
    ap.add_argument("--tools", default="", help="comma-separated tools the host called, from the production log")
    args = ap.parse_args(argv)
    cases = load_cases()
    if args.case not in cases:
        print(f"unknown case {args.case!r}; known: {', '.join(cases)}", file=sys.stderr)
        return 2
    if args.codex:
        tools, answer = read_codex(args.codex.read_text(encoding="utf-8"))
    else:
        tools, answer = [t for t in args.tools.split(",") if t], args.text.read_text(encoding="utf-8")
    rows = score(cases[args.case], tools, answer)
    for r in rows:
        print(("PASS " if r["ok"] else "FAIL ") + r["check"] + (f"  [{r['detail']}]" if r["detail"] else ""))
    ok = all(r["ok"] for r in rows)
    print(f"{args.case}: {'PASS' if ok else 'FAIL'} ({sum(r['ok'] for r in rows)}/{len(rows)} checks), {len(tools)} tool calls")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""The repository process written down in docs/triage.md is real (issue #131): the label decisions are recorded, the relabel
script is a dry run unless told otherwise, and the issue templates pre-apply status:needs-refinement."""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import relabel_issues as relabel  # noqa: E402

TRIAGE = (ROOT / "docs" / "triage.md").read_text(encoding="utf-8")
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"


def label(name):
    return {"name": name}


def test_the_label_decisions_are_recorded_for_all_three_renames():
    decisions = TRIAGE.split("## Label decisions", 1)[1].split("\n## ", 1)[0]
    for old, new in (("enhancement", "type:feature"), ("data", "area:data")):
        assert re.search(rf"`{old}`\s*\|\s*`{new}`\s*\|\s*\*\*rename\*\*", decisions), (old, new)
    assert re.search(r"`in-progress`\s*\|\s*`status:in-progress`\s*\|\s*\*\*not renamed\*\*", decisions)
    assert "2026-10-07" in decisions and "scripts/relabel_issues.py" in decisions


def test_the_scripts_renames_are_the_recorded_ones():
    assert relabel.RENAMES == {"enhancement": "type:feature", "data": "area:data"}
    assert "in-progress" not in relabel.RENAMES


def test_a_plan_adds_the_new_label_and_removes_the_old_one_and_handles_items_with_both():
    items = [
        {"number": 62, "title": "gate", "state": "OPEN", "labels": [label("data"), label("area:data"), label("P1")]},
        {"number": 133, "title": "cache", "state": "OPEN", "labels": [label("data"), label("area:backend")]},
        {"number": 7, "title": "idea", "state": "CLOSED", "labels": [label("enhancement")]},
        {"number": 8, "title": "clean", "state": "OPEN", "labels": [label("type:feature")]},
    ]
    changes = relabel.plan(items, relabel.RENAMES, "issue")
    by_number = {c["number"]: c for c in changes}
    assert set(by_number) == {7, 62, 133}  # #8 carries no old label
    assert by_number[62]["add"] == [] and by_number[62]["remove"] == ["data"]  # already had area:data
    assert by_number[133]["add"] == ["area:data"] and by_number[133]["remove"] == ["data"]
    assert by_number[7]["add"] == ["type:feature"] and by_number[7]["remove"] == ["enhancement"]
    assert relabel.command(by_number[133], "o/r") == ["issue", "edit", "133", "-R", "o/r", "--add-label", "area:data",
                                                       "--remove-label", "data"]


class FakeGh:
    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            label_name = args[args.index("--label") + 1]
            if label_name == "data":
                return json.dumps([{"number": 133, "title": "cache", "state": "OPEN",
                                    "labels": [label("data"), label("area:backend")]}])
            return "[]"
        if args[:2] == ("pr", "list"):
            return "[]"
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": "area:data"}, {"name": "type:feature"}, {"name": "data"}, {"name": "enhancement"}])
        return ""


def test_the_default_is_a_dry_run_that_only_reads_and_prints_the_exact_list(capsys):
    gh = FakeGh()
    assert relabel.main([], run=gh) == 0
    out = capsys.readouterr().out
    assert "issue #133" in out and "add ['area:data'], remove ['data']" in out and "Dry run: nothing was changed" in out
    assert all(call[1] == "list" for call in gh.calls), gh.calls  # no edit, no delete


def test_apply_edits_each_listed_item_and_deleting_old_labels_needs_apply(capsys):
    gh = FakeGh()
    assert relabel.main(["--apply"], run=gh) == 0
    edits = [c for c in gh.calls if c[1] == "edit"]
    assert edits == [("issue", "edit", "133", "-R", relabel.REPO, "--add-label", "area:data", "--remove-label", "data")]
    assert not [c for c in gh.calls if c[:2] == ("label", "delete")]
    gh = FakeGh()
    relabel.main(["--apply", "--delete-old-labels"], run=gh)
    assert [c[2] for c in gh.calls if c[:2] == ("label", "delete")] == ["enhancement", "data"]
    try:
        relabel.main(["--delete-old-labels"], run=FakeGh())
    except SystemExit as stop:
        assert stop.code == 2
    else:
        raise AssertionError("--delete-old-labels without --apply must be refused")


def test_apply_refuses_when_a_new_label_does_not_exist():
    class NoLabels(FakeGh):
        def __call__(self, *args):
            if args[:2] == ("label", "list"):
                return json.dumps([{"name": "data"}])
            return super().__call__(*args)

    gh = NoLabels()
    assert relabel.main(["--apply"], run=gh) == 1
    assert not [c for c in gh.calls if c[1] == "edit"]


# -- issue templates

def load_template(name):
    return yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))


def triage_labels():
    return set(re.findall(r"`((?:area|type|status):[a-z-]+|bug|P[123]|in-progress)`", TRIAGE))


def test_both_templates_pre_apply_status_needs_refinement_and_a_known_type():
    bug, feature = load_template("bug_report.yml"), load_template("feature_request.yml")
    assert bug["labels"] == ["bug", "status:needs-refinement"]
    assert feature["labels"] == ["type:feature", "status:needs-refinement"]
    for template in (bug, feature):
        assert set(template["labels"]) <= triage_labels(), "templates may only apply labels docs/triage.md defines"
        ids = {part.get("id") for part in template["body"]}
        assert {"criteria"} <= ids, "acceptance criteria come first (AGENTS.md)"
        assert any(part.get("validations", {}).get("required") for part in template["body"])


def test_new_issues_cannot_skip_the_forms_and_the_connect_page_is_offered():
    config = load_template("config.yml")
    assert config["blank_issues_enabled"] is False
    assert [link["url"] for link in config["contact_links"]] == ["https://mtgvault.cards/connect.html"]
    assert (ROOT / "public" / "connect.html").exists()

"""#30: `whoami` is used in every onboarding doc and by every skill, and the docs do not claim the first-run prompt exists."""

from pathlib import Path

import pytest
import yaml

from vault.api import mcp
from vault.api.mcp_catalog import PROMPTS

ROOT = Path(__file__).parent.parent
ONBOARDING_DOCS = ["README.md", "docs/onboarding.md", "docs/mcp-oauth-host-checklist.md", "docs/skills.md", "docs/agents.md",
                   "public/connect.html", "plugins/the-vault/README.md", "public/llms.txt"]
SKILLS = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())


@pytest.mark.parametrize("path", ONBOARDING_DOCS)
def test_every_onboarding_doc_names_whoami(path):
    assert "whoami" in (ROOT / path).read_text(encoding="utf-8"), f"{path} does not tell the reader to check the connection with whoami"


def test_the_host_checklist_asks_for_whoami_in_both_hosts_and_checks_the_rules_edition():
    text = (ROOT / "docs" / "mcp-oauth-host-checklist.md").read_text(encoding="utf-8")
    chatgpt, claude = text.split("## ChatGPT (developer mode)")[1].split("## Claude.ai (custom connector)")
    assert "whoami" in chatgpt and "whoami" in claude
    assert "rules_version" in chatgpt  # the cold-instance null of #244 is something a person checks on the real host


@pytest.mark.parametrize("folder", SKILLS, ids=lambda p: p.name)
def test_every_skill_declares_and_names_whoami(folder):
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    front, body = text[4:].split("\n---\n", 1)
    assert "whoami" in yaml.safe_load(front)["metadata"]["vault-tools"].split(), f"{folder.name} does not declare whoami"
    assert "`whoami`" in body, f"{folder.name} never tells the assistant when to call whoami"


def test_the_plugin_copies_of_the_skills_carry_it_too():
    for folder in SKILLS:
        copy = ROOT / "plugins" / "the-vault" / "skills" / folder.name / "SKILL.md"
        assert "`whoami`" in copy.read_text(encoding="utf-8"), copy


def test_the_onboarding_design_does_not_claim_the_first_run_prompt_exists():
    """docs/onboarding.md says whoami is called by the vault_start prompt, which is a design: it must not be in PROMPTS unannounced,
    and the doc must keep saying it is not built."""
    doc = (ROOT / "docs" / "onboarding.md").read_text(encoding="utf-8")
    assert "vault_start" in doc and "Nothing here is built" in doc
    if "vault_start" not in {p["name"] for p in PROMPTS}:
        assert "is still only this design and is not in `PROMPTS`" in doc


def test_whoami_is_a_tool_the_server_lists():
    assert "whoami" in mcp.BY_NAME

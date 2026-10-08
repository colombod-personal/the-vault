"""The MCP Registry description of the remote server (server.json, #60): shaped to the registry's schema, and saying the
same address and version as the rest of the repository, so publishing it needs only the owner's namespace login."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_it_follows_the_registry_schema_rules():
    assert SERVER["$schema"].startswith("https://static.modelcontextprotocol.io/schemas/") and SERVER["$schema"].endswith("/server.schema.json")
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", SERVER["name"]) and 3 <= len(SERVER["name"]) <= 200
    assert 1 <= len(SERVER["description"]) <= 100
    assert re.fullmatch(r"\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?", SERVER["version"]), "a plain version, not a range"
    assert SERVER["repository"]["source"] == "github" and SERVER["repository"]["url"].startswith("https://github.com/")
    (remote,) = SERVER["remotes"]
    assert remote["type"] == "streamable-http" and re.fullmatch(r"https?://[^\s]+", remote["url"])
    assert "packages" not in SERVER  # a hosted server: nothing to install


def test_the_namespace_is_the_github_account_that_owns_the_repository():
    namespace = SERVER["name"].split("/")[0]
    assert namespace == "io.github." + SERVER["repository"]["url"].split("/")[3]


def test_it_names_the_same_address_and_version_as_the_plugin_and_the_project():
    plugin = json.loads((ROOT / "plugins" / "the-vault" / "mcp.json").read_text(encoding="utf-8"))
    urls = {s["url"] for s in plugin["mcpServers"].values()}
    assert SERVER["remotes"][0]["url"] in urls
    assert SERVER["version"] == tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

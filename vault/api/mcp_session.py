"""What a client told the server at ``initialize``, carried in a signed ``Mcp-Session-Id`` (no server-side state).

The MCP server is stateless, so it cannot remember that a client advertised the MCP Apps extension
(``io.modelcontextprotocol/ui``) when it connected. Streamable HTTP lets a server hand out an ``Mcp-Session-Id`` at
``initialize``, which the client sends back on every later request. Here that id *is* the memory: it holds one bit
(did the client advertise the extension?), a random part and an HMAC under the server's secret. Nothing is stored,
nothing identifies the person, and any server instance can read it.

Fail open: a request with no session id, an id that does not verify (another secret, a typo) or one from before this
existed is treated as "unknown", and ``tools/list`` then behaves as it always did (the views are advertised). Only an
id this server signed, saying the client connected **without** the extension, hides the views: a host that never said it
can show them gets the plain tool list, which is all the specification promises it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

UI_EXTENSION = "io.modelcontextprotocol/ui"
UI_MIME = "text/html;profile=mcp-app"
HEADER = "Mcp-Session-Id"
_VERSION = "v1"


def advertises_ui(capabilities: object) -> bool:
    """Whether the client's ``initialize`` capabilities carry the MCP Apps extension (ext-apps, 2026-01-26:
    ``capabilities.extensions["io.modelcontextprotocol/ui"] = {"mimeTypes": ["text/html;profile=mcp-app"]}``).
    Early drafts of the extension sat under ``experimental``; both are read. A ``mimeTypes`` list that does not
    include the MCP App type means the host cannot show our pages."""
    if not isinstance(capabilities, dict):
        return False
    for group in ("extensions", "experimental"):
        found = capabilities.get(group)
        if not isinstance(found, dict) or not isinstance(found.get(UI_EXTENSION), dict):
            continue
        types = found[UI_EXTENSION].get("mimeTypes")
        if types is None or (isinstance(types, list) and UI_MIME in types):
            return True
    return False


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), f"mcp-session:{body}".encode(), hashlib.sha256).hexdigest()[:32]


def issue(secret: str, ui: bool) -> str:
    """A new session id for a client that did (or did not) advertise the MCP Apps extension."""
    body = f"{_VERSION}.{'1' if ui else '0'}.{secrets.token_hex(8)}"
    return f"{body}.{_sign(secret, body)}"


def read(secret: str, header: str | None) -> bool | None:
    """True or False: what this server's own session id says about the extension. None: no usable id (unknown)."""
    if not header or len(header) > 100:
        return None
    parts = header.split(".")
    if len(parts) != 4 or parts[0] != _VERSION or parts[1] not in ("0", "1"):
        return None
    body = ".".join(parts[:3])
    if not hmac.compare_digest(parts[3], _sign(secret, body)):
        return None
    return parts[1] == "1"

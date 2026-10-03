"""Run the twin universe: ``python -m twins --port 9000``.

It reads the vault's settings from the environment (the same ``.env`` values) and registers the
vault's OAuth apps with the identity twins. Then start the vault with
``VAULT_TWINS_URL=http://localhost:9000``. Open http://localhost:9000/_twins for the control panel.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from vault.config import Settings

from .server import create_server
from .universe import Universe

DEMO_ACCOUNTS = [("ann", "ann@example.com", "Ann Example"), ("bo", "bo@example.com", "Bo Example")]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the twin universe")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TWINS_PORT", 9000)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--public-url", help="how browsers reach this server (default http://localhost:PORT)")
    parser.add_argument("--rate-limits", action="store_true", help="enforce Scryfall's rate limits (429 + lockout)")
    args = parser.parse_args(argv)

    universe = Universe(enforce_rate_limits=args.rate_limits)
    universe.register_vault(Settings())
    for twin in universe.identity.values():
        for sub, email, name in DEMO_ACCOUNTS:
            twin.add_account(f"{twin.name}-{sub}", email, name)
    universe.archidekt.add_deck("Twin demo deck", "twin-user", [(1, "Sol Ring"), (4, "Lightning Bolt"), (1, "Rhystic Study")])
    public = args.public_url or f"http://localhost:{args.port}"
    print(f"Twin universe on {public}/_twins")
    uvicorn.run(create_server(universe, public), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

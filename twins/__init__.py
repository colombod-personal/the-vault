"""A digital twin universe for The Vault.

Behavioural clones of every third-party service the Vault talks to: Google, Microsoft, Apple
and Facebook sign-in, Scryfall, Archidekt, Vercel's API (for the setup job), and 17Lands' public data files. They answer
on the real host names, keep state and follow the real services' rules. The Vault is tested against them without touching the
real services, and developers can run the whole app offline against them. See ``docs/twins.md``.

    from twins import Universe
    universe = Universe()
    app = create_app(settings, transport=universe.transport)
"""

from .base import Fault, Twin
from .identity import Account, Callback
from .universe import Universe

__all__ = ["Account", "Callback", "Fault", "Twin", "Universe"]

"""Which modules count requests in clock-aligned one-minute windows, and how the tests freeze their clock (#112).

A per-minute budget counts hits in the window ``int(time.time() // 60)``. A burst of requests that crosses a minute boundary
starts a fresh window, so the 429 or 503 a test expects never comes: a test that fails about once in a few hundred runs.
The autouse fixture in ``conftest.py`` freezes the ``time`` the module sees (and only that: ``time.monotonic`` and everything
else stay real) at the start of every test, for every module in :data:`FROZEN_CLOCK_MODULES`.
``tests/test_clock_is_frozen.py`` reads the source and fails when another module starts counting per minute without being
listed here, so the next one cannot be forgotten."""

from __future__ import annotations

# Modules that call ``time.time()`` to pick a per-minute window for a counter.
FROZEN_CLOCK_MODULES = (
    "vault.ratelimit",       # sign-in and OAuth endpoint limits, the per-user refresh limit
    "vault.api.catalog_api",  # catalog and deck-analysis limit
    "vault.oauth_clients",   # the shared and per-caller budgets for fetching a client's metadata document
)

# Modules that call ``hit()`` or floor-divide by 60 but whose window does not depend on the clock, with the reason.
CLOCK_INDEPENDENT = {
    "vault.client_auth": "the counter row is keyed by the assertion's own jti and expiry (exp // 60), one use each; "
                         "no budget is counted in a window that the clock moves",
}


class FrozenClock:
    """A stand-in for the ``time`` module whose ``time()`` always answers one instant; everything else is the real module's."""

    def __init__(self, real, now: float):
        self._real = real
        self.time = lambda: now

    def __getattr__(self, name):
        return getattr(self._real, name)

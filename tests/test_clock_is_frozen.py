"""No per-minute budget can flake across a minute boundary (#112, and the same class in every other counter).

Every module that counts requests in a clock-aligned one-minute window must have its clock frozen by the autouse fixture in
``conftest.py`` (``tests/frozen_clock.py`` is the list). These tests read the source of the Vault, so a new module that starts
counting per minute fails here until it is listed, instead of failing once in a few hundred CI runs."""

import ast
import importlib
import time
from pathlib import Path

from frozen_clock import CLOCK_INDEPENDENT, FROZEN_CLOCK_MODULES

ROOT = Path(__file__).resolve().parent.parent
WINDOW_DIVISORS = {60}  # seconds in the window; ratelimit.WINDOW is 60


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def counts_per_minute(tree: ast.AST) -> bool:
    """True when the module picks a one-minute window from a clock (``time.time() // 60`` or ``// WINDOW``) or calls hit()."""
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.FloorDiv):
            right = node.right
            if (isinstance(right, ast.Constant) and right.value in WINDOW_DIVISORS) or (
                    isinstance(right, ast.Name) and right.id == "WINDOW"):
                return True
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "hit") or (isinstance(func, ast.Attribute) and func.attr == "hit"):
                return True
    return False


def test_every_module_that_counts_per_minute_has_a_frozen_clock():
    counting = set()
    for path in (ROOT / "vault").rglob("*.py"):
        if "migrations" in path.parts:
            continue
        if counts_per_minute(ast.parse(path.read_text(encoding="utf-8"))):
            counting.add(module_name(path))
    unlisted = counting - set(FROZEN_CLOCK_MODULES) - set(CLOCK_INDEPENDENT)
    assert not unlisted, (
        f"{sorted(unlisted)} count requests per minute: add them to FROZEN_CLOCK_MODULES in tests/frozen_clock.py "
        "(and give them a module-level `import time`), or to CLOCK_INDEPENDENT with the reason. Otherwise a test that "
        "expects a 429 or 503 fails whenever its burst crosses a minute boundary.")
    stale = (set(FROZEN_CLOCK_MODULES) | set(CLOCK_INDEPENDENT)) - counting
    assert not stale, f"{sorted(stale)} are listed but no longer count per minute: remove them"


def test_the_clock_of_every_listed_module_is_frozen_for_the_test_but_only_its_time():
    start = time.time()
    clocks = []
    for name in FROZEN_CLOCK_MODULES:
        module = importlib.import_module(name)
        assert module.time is not time, f"{name}: the autouse fixture did not replace its clock"
        first = module.time.time()
        time.sleep(0.02)
        assert module.time.time() == first, f"{name}: the clock moved during the test"
        assert abs(first - start) < 5
        before = module.time.monotonic()
        time.sleep(0.05)
        assert module.time.monotonic() > before, f"{name}: only time() is frozen; monotonic must keep running"
        clocks.append(module.time)
    assert all(c is clocks[0] for c in clocks), "one shared clock: a test that moves it moves every counter"


def test_a_burst_through_the_metadata_budget_is_counted_in_one_window(database_url):
    """The OAuth metadata-fetch budget reads its window from ``oauth_clients.time``: with the clock frozen every hit of a
    burst lands in the same window, so the budget runs out exactly when the test expects (it did not before #112's fix
    reached this module: a burst over a minute boundary started a fresh budget and the expected 503 never came)."""
    import pytest

    from vault.db import Database
    from vault.oauth_clients import ClientError, _spend_fetch_budget

    db = Database(database_url)
    try:
        with db.sessions() as session:
            _spend_fetch_budget(session, 2, "burst")
            time.sleep(0.02)
            _spend_fetch_budget(session, 2, "burst")
            with pytest.raises(ClientError) as refused:
                _spend_fetch_budget(session, 2, "burst")
            assert refused.value.code == "temporarily_unavailable"
    finally:
        db.engine.dispose()

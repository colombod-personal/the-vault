"""Scryfall ids for tests. Real ids are UUIDs and the price table's key is a native uuid (#63), so a test's printing
needs one too: ``sid("sol")`` is a stable UUID for the name ``sol``."""

import uuid


def sid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"the-vault-tests/{name}"))

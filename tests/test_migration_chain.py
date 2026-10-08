"""The migration chain has exactly one head and no gaps: two pull requests that take the same number, or a number whose parent
is missing, fail here instead of at deploy time (a second head makes Database.migrate refuse to start)."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import vault.db


def script():
    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    return ScriptDirectory.from_config(config)


def test_there_is_one_head_and_every_revision_has_its_parent():
    s = script()
    assert len(s.get_heads()) == 1, f"two migrations share a parent: {s.get_heads()}"
    revisions = {r.revision: r for r in s.walk_revisions()}
    for r in revisions.values():
        for parent in ([r.down_revision] if isinstance(r.down_revision, str) else list(r.down_revision or ())):
            assert parent in revisions, f"{r.revision} follows {parent}, which does not exist"
    numbered = [int(r) for r in revisions if r.isdigit()]
    assert len(numbered) == len(set(numbered))


def test_the_price_compaction_follows_the_migration_before_it_and_there_is_one_head():
    s = script()
    compaction = s.get_revision("0113")
    assert len(s.get_heads()) == 1
    assert compaction.down_revision == max(r.revision for r in s.walk_revisions() if r.revision < "0113"),         "set down_revision in vault/migrations/versions/0113_compact_price_snapshots.py to the migration numbered just before it"

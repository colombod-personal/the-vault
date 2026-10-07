"""The answers a re-import takes (vault.merge): shared by the import, its preview and the staged upload's preview and apply."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query

from ..importer import ImportOptions

ReplaceEverything = Annotated[bool, Query(description="Replace the whole collection with the file and discard edits made in "
                                          "the Vault (the old behaviour). Default: apply only what changed in the person's app")]
Conflicts = Annotated[Literal["vault", "app"], Query(description="The answer for every card changed both in the app and in "
                                                     "the Vault: keep the Vault's edit (default) or take the app's value")]
UseAppValue = Annotated[list[str] | None, Query(max_length=500, description="Conflict ids (from the preview) to answer "
                                                "'app' for, whatever `conflicts` says")]


def import_options(replace_everything: ReplaceEverything = False, conflicts: Conflicts = "vault",
                   use_app_value: UseAppValue = None) -> ImportOptions:
    return ImportOptions(replace_everything=replace_everything, conflicts=conflicts,
                         use_app_value=frozenset(i[:40] for i in use_app_value or ()))

"""The answers a re-import takes (vault.merge): shared by the import, its preview and the staged upload's preview and apply."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Literal

from fastapi import Query

from ..importer import ImportOptions
from .schemas import MAX_ID

ReplaceEverything = Annotated[bool, Query(description="Replace the whole collection with the file and discard edits made in "
                                          "the Vault (the old behaviour). Default: apply only what changed in the person's app")]
Conflicts = Annotated[Literal["vault", "app"], Query(description="The answer for every card changed both in the app and in "
                                                     "the Vault: keep the Vault's edit (default) or take the app's value")]
UseAppValue = Annotated[list[str] | None, Query(max_length=500, description="Conflict ids (from the preview) to answer "
                                                "'app' for, whatever `conflicts` says")]


BucketId = Annotated[int | None, Query(ge=1, le=MAX_ID, description="Import into this bucket only (GET /collection/buckets): the "
                                       "file is compared with that bucket's copies and replaces only them; every other bucket, its "
                                       "tags and its notes stay as they are, and the file's cards land in this bucket whatever "
                                       "folder the file names. Default: the whole collection. Another person's id is a 404")]


def answer_options(replace_everything: ReplaceEverything = False, conflicts: Conflicts = "vault",
                   use_app_value: UseAppValue = None) -> ImportOptions:
    """The answers alone: a staged upload takes its bucket from the link (vault.uploads), never from the request."""
    return ImportOptions(replace_everything=replace_everything, conflicts=conflicts,
                         use_app_value=frozenset(i[:40] for i in use_app_value or ()))


def import_options(bucket_id: BucketId = None, replace_everything: ReplaceEverything = False, conflicts: Conflicts = "vault",
                   use_app_value: UseAppValue = None) -> ImportOptions:
    return replace(answer_options(replace_everything, conflicts, use_app_value), bucket_id=bucket_id)

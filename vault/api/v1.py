"""The Vault API, version 1 (``/api/v1``), for the web app and native apps.

Start at ``GET /api/v1`` and follow ``_links``. Authentication: the web app's session
cookie, or ``Authorization: Bearer <access_token>`` for native apps (see ``/auth``).
Every list is cursor-paginated; no response grows with the size of a collection
beyond one page (max 500 items), except the export downloads.

Tenant isolation: queries are scoped to the signed-in user; other users' data is
reachable only through shares they granted, and ids that aren't yours answer 404.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import Response
from mtg_toolkits import decklist, delta, normalize_set_code
from mtg_toolkits.formats import FORMATS
from mtg_toolkits.archidekt import ArchidektClient
from mtg_toolkits.normalize import SET_ALIAS_PREFIXES, set_alias_map
from mtg_toolkits.http import ApiError
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import analytics, deck_text, oauth_server, outbound, tags as card_tags, tokens
from ..catalog import Catalog
from ..auth import IdentityInUse, Profile, find_or_create
from ..deck_tools import loose_name
from ..collection_view import SORTS, CollectionView, filtered, filtered_printing, finite, history_days, import_days, view_version
from .. import archidekt_cache, deck_import, deck_match, deck_overview, deck_refresh, deck_versions, experts, owned_changes
from ..importer import (MAX_UPLOAD_BYTES, ImportConflict, ImportError_, ImportOptions, export_collection, import_collection,
                        preview_import as preview_collection_import, user_entries)
from ..models import AccessToken, ApiSession, Bucket, Card, Deck, Import, NativeNonce, OAuthClient, OAuthGrant, Passkey, PriceSnapshot, Share, User
from ..native import LEEWAY as NATIVE_LEEWAY, NativeTokenError, NativeVerifier, ProviderUnavailable
from ..passkeys import remove_passkey
from ..prices import compute_values
from ..privacy import export_archive, purge_user
from ..ratelimit import limited, per_user
from ..sharing import accept_invite, create_invite, display_name, incoming_share, invite_again, owned_deck
from . import schemas as S
from .hal import clamp_limit, decode_cursor, encode_cursor, etag_response, link, page_body, paginate
from .idempotency import idempotent
from .import_params import import_options

V1 = "/api/v1"
Id = Annotated[int, Path(ge=1, le=S.MAX_ID)]  # a row id: anything larger can't exist (and would overflow the column)
DELETE_CONFIRMATION = "DELETE"
MAX_COPY_ROWS = 500  # rows listed in one card's detail; copies_total says how many there are
MAX_STATS = 50  # items per list in /collection/stats
# The filters the collection's analytics take (#130): the person's own grouping, so not on a shared collection.
BucketQ = Annotated[int | None, Query(ge=1, le=S.MAX_ID, description="Only the copies in this bucket (GET /collection/buckets); "
                                      "not on a shared collection")]
TagQ = Annotated[str | None, Query(max_length=40, description="Only cards you tagged with this (GET /collection/tags): every printing "
                                   "of a tagged card counts, once; an unknown tag gives an empty answer; not on a shared collection")]


def _scryfall_set(code: str) -> str:
    """The Scryfall set code a collection's set code stands for (Dragon Shield's GK2_ORZHOV is gk2)."""
    return (normalize_set_code(code) or code).lower()


def _ordinal(day: str | None) -> int | None:
    return date.fromisoformat(day).toordinal() if day else None


# /collection/sets sorts (keys for hal.paginate; ties broken by set code). Unreleased last both ways.
SET_SORTS = {
    "-value": lambda s: (-s["market_value"],), "value": lambda s: (s["market_value"],),
    "-quantity": lambda s: (-s["copies"],), "quantity": lambda s: (s["copies"],),
    "-unique": lambda s: (-s["printings"],), "unique": lambda s: (s["printings"],),
    "name": lambda s: ((s["name"] or "").lower(),), "code": lambda s: (s["code"].lower(),),
    "release": lambda s: (_ordinal(s["released_at"]) or 10**7,),
    "-release": lambda s: (-(_ordinal(s["released_at"]) or -10**7),),
}


def scope_query(bucket_id: int | None, tag: str | None) -> str:
    """``?bucket=3&tag=trade`` (or nothing): the selection an analytics answer was limited to, kept in its links."""
    params = {**({"bucket": bucket_id} if bucket_id is not None else {}), **({"tag": tag} if tag is not None else {})}
    return f"?{urlencode(params)}" if params else ""


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def build_router(get_db, current_user, optional_user, settings, verifier: NativeVerifier, auth_providers,
                 transport=None, account_user=None) -> APIRouter:
    account_user = account_user or current_user
    router = APIRouter(prefix=V1)

    # -- entry point ------------------------------------------------------------------------
    @router.get("", tags=["root"], summary="API entry point: follow the links")
    def root(user: User | None = Depends(optional_user)) -> dict:
        links = {
            "self": link(V1),
            "docs": link("/api/docs", title="Interactive documentation"),
            "openapi": link("/api/openapi.json"),
            "auth": link(f"{V1}/auth", title="Sign-in methods for web and native apps"),
        }
        if user:
            links |= {
                "me": link(f"{V1}/me"), "collection": link(f"{V1}/collection"),
                "cards": link(f"{V1}/collection/cards"), "imports": link(f"{V1}/imports"),
                "decks": link(f"{V1}/decks"), "shares": link(f"{V1}/shares"), "shared": link(f"{V1}/shared"),
            }
        links |= {
            "mcp": link("/api/mcp", title="Model Context Protocol server for AI agents (Streamable HTTP)"),
            "llms": link("/llms.txt", title="How to use this API, written for AI agents"),
        }
        if user:
            links["tokens"] = link(f"{V1}/me/tokens", title="Personal access tokens for agents and scripts")
        if settings.twins_url:  # local development: the front end sends its Scryfall calls there too
            links["twins"] = link(settings.twins_url, title="Digital twin universe (development)")
        return {"version": "1", "signed_in": user is not None, "_links": links}

    # -- auth for native apps -------------------------------------------------------------------
    @router.get("/auth", tags=["auth"], summary="How to sign in (web and native)")
    def auth_info() -> dict:
        return {
            "web_providers": auth_providers(),
            "native_providers": verifier.enabled,
            "app_redirect_uris": list(settings.app_redirect_uris),
            "_links": {
                "self": link(f"{V1}/auth"),
                "native": link(f"{V1}/auth/native/{{provider}}", title="Exchange a native SDK ID token (apple, google)"),
                "browser": link("/api/auth/login/{provider}?app_redirect_uri={uri}&code_challenge={S256}",
                                title="Browser sign-in handed back to the app with a one-time code"),
                "token": link(f"{V1}/auth/token", title="Redeem a code or refresh token"),
                "revoke": link(f"{V1}/auth/revoke", title="Sign this app out"),
            },
        }

    @router.post("/auth/native/{provider}", tags=["auth"], response_model=S.TokenResponse,
                 summary="Sign in with a native Apple or Google ID token", dependencies=limited("native", verify=True))
    async def native_sign_in(provider: str, body: S.NativeSignIn, request: Request, db: Session = Depends(get_db),
                             current: User | None = Depends(optional_user)) -> dict:
        try:
            claims = await verifier.verify(provider, body.id_token, body.nonce)
        except NativeTokenError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ProviderUnavailable as exc:
            raise HTTPException(503, f"{exc}. Try again shortly.", headers={"Retry-After": "30"}) from exc
        # Each ID token signs in once: the nonce inside it is recorded, and committed on its own
        # so nothing later in the sign-in can roll it back, until the token can no longer be
        # used (its expiry plus the clock leeway ``verify`` allows).
        db.execute(delete(NativeNonce).where(NativeNonce.expires < time.time()))
        db.add(NativeNonce(id=hashlib.sha256(f"{provider}:{claims['nonce']}".encode()).hexdigest(),
                           expires=float(claims["exp"]) + NATIVE_LEEWAY))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(401, "This sign-in was already used; sign in again") from None
        name = body.name if provider == "apple" else claims.get("name")
        if "account" not in request.state.scopes:  # a personal access token can't add sign-in methods
            current = None
        try:
            user = find_or_create(db, Profile(provider, str(claims["sub"]), claims.get("email"), name), current)
        except IdentityInUse as exc:
            raise HTTPException(409, str(exc)) from exc
        return tokens.issue(db, user, "app", body.device_name)

    @router.post("/auth/token", tags=["auth"], response_model=S.TokenResponse,
                 summary="Redeem a browser sign-in code (PKCE) or rotate a refresh token",
                 dependencies=limited("token", verify=True))
    def token(body: S.TokenRequest, db: Session = Depends(get_db)) -> dict:
        try:
            if body.grant_type == "refresh_token":
                if not body.refresh_token:
                    raise HTTPException(400, "refresh_token is required")
                return tokens.refresh(db, body.refresh_token)
            if not (body.code and body.code_verifier and body.redirect_uri):
                raise HTTPException(400, "code, code_verifier and redirect_uri are required")
            return tokens.redeem_code(db, body.code, body.code_verifier, body.redirect_uri, body.device_name)
        except tokens.TokenError as exc:
            raise HTTPException(400, exc.description, headers={"X-Error": exc.error}) from exc

    @router.post("/auth/revoke", tags=["auth"], summary="Sign this app out (revokes its tokens)")
    def revoke(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
        bearer = getattr(request.state, "bearer", None)
        if not bearer:
            raise HTTPException(400, "Only bearer-token sessions can be revoked here; web sessions use /api/auth/logout")
        if tokens.is_pat(bearer):
            db.execute(delete(AccessToken).where(AccessToken.token_hash == tokens._hash(bearer)))
            db.commit()
            return {"revoked": True}
        return {"revoked": tokens.revoke_by_access(db, bearer)}

    # -- account ------------------------------------------------------------------------------------
    def _me(user: User) -> dict:
        return {
            "id": user.id, "name": user.name, "email": user.email,
            "providers": sorted({i.provider for i in user.identities}),
            "_links": {"self": link(f"{V1}/me"), "sessions": link(f"{V1}/me/sessions"),
                       "apps": link(f"{V1}/me/apps", title="Apps connected with OAuth (ChatGPT, Claude, ...)"),
                       "tokens": link(f"{V1}/me/tokens", title="Personal access tokens for agents and scripts"),
                       "passkeys": link(f"{V1}/me/passkeys", title="Passkeys that can sign in to this account"),
                       "export": link(f"{V1}/me/export", title="Download all my data (ZIP)"),
                       "collection": link(f"{V1}/collection")},
        }

    @router.get("/me", tags=["account"], response_model=S.Me)
    def me(user: User = Depends(current_user)) -> dict:
        return _me(user)

    @router.patch("/me", tags=["account"], response_model=S.Me)
    def update_me(body: S.ProfileUpdate, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        user.name = body.name.strip()[:200] or None  # right to rectification
        db.commit()
        return _me(user)

    @router.get("/me/export", tags=["account"], summary="Everything held about you, as a ZIP (GDPR)")
    def export_me(user: User = Depends(account_user), db: Session = Depends(get_db)) -> Response:
        name = f"vault-data-{date.today().isoformat()}.zip"
        return Response(export_archive(db, user), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @router.delete("/me", tags=["account"], summary='Delete the account and all its data ({"confirm": "DELETE"})')
    def delete_me(body: S.DeleteRequest, request: Request, user: User = Depends(account_user),
                  db: Session = Depends(get_db)) -> dict:
        if body.confirm != DELETE_CONFIRMATION:
            raise HTTPException(400, f'Send {{"confirm": "{DELETE_CONFIRMATION}"}} to delete your account')
        removed = purge_user(db, user.id)
        request.session.clear()
        return {"deleted": True, "removed": removed}

    @router.get("/me/sessions", tags=["account"], response_model=S.SessionPage,
                summary="Apps signed in to this account")
    def sessions(request: Request, cursor: str | None = None, limit: int | None = None,
                 user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(ApiSession).where(ApiSession.user_id == user.id)))
        current = tokens._hash(request.state.bearer) if getattr(request.state, "bearer", None) else None
        page, nxt = paginate(rows, lambda s: (-s.id,), lambda s: s.id, cursor=cursor, limit=limit)
        items = [{"id": s.id, "client": s.client, "device_name": s.device_name, "created_at": _iso(s.created_at),
                  "last_used_at": _iso(s.last_used_at), "current": s.access_hash == current,
                  "_links": {"self": link(f"{V1}/me/sessions/{s.id}")}} for s in page]
        return page_body(request, items, nxt, len(rows), limit=limit)

    @router.delete("/me/sessions/{session_id}", tags=["account"], summary="Sign an app out remotely")
    def end_session(session_id: Id, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        s = db.get(ApiSession, session_id)
        if s is None or s.user_id != user.id:
            raise HTTPException(404, "Session not found")
        db.delete(s)
        db.commit()
        return {"deleted": True}

    # -- connected apps (OAuth grants to AI apps such as ChatGPT and Claude) ----------------------
    def _app(group: list[OAuthGrant], client: OAuthClient | None) -> dict:
        """One row per app: the newest connection speaks for it (name, scopes), the rest are counted."""
        newest = group[0]
        domain = urlsplit(newest.client_id).hostname if newest.client_id.startswith("https://") else None
        used = [g.last_used_at for g in group if g.last_used_at]
        idle = [g for g in group if oauth_server.is_idle(g)]
        return {"id": newest.id, "name": client.name if client else (domain or "Unknown app"), "domain": domain,
                "verified_by_address": domain is not None,
                "scopes": [s for s in oauth_server.SCOPES if any(s in g.scopes.split() for g in group)],
                "connections": len(group), "connection_ids": sorted(g.id for g in group),
                "created_at": _iso(min(g.created_at for g in group)), "last_used_at": _iso(max(used)) if used else None,
                "idle": len(idle) == len(group), "idle_connections": len(idle),
                "used_minutes_ago": oauth_server.minutes_since_use(group),
                "_links": {"self": link(f"{V1}/me/apps/{newest.id}")}}

    @router.get("/me/apps", tags=["account"], response_model=S.ConnectedAppPage,
                summary="Apps connected to your account with OAuth (ChatGPT, Claude, ...): one row per app, however many times it was connected")
    def list_apps(request: Request, cursor: str | None = None, limit: int | None = None,
                  user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        rows = oauth_server.connected_apps(db, user.id)
        names = {c.client_id: c for c in db.scalars(select(OAuthClient).where(
            OAuthClient.client_id.in_({g.client_id for grp in rows for g in grp})))} if rows else {}
        page, nxt = paginate(rows, lambda grp: (-grp[0].id,), lambda grp: grp[0].id, cursor=cursor, limit=limit)
        return page_body(request, [_app(grp, names.get(grp[0].client_id)) for grp in page], nxt, len(rows), limit=limit)

    @router.delete("/me/apps/{app_id}", tags=["account"],
                   summary="Disconnect an app: every connection of it (each device) stops working at once")
    def disconnect_app(app_id: Id, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        revoked = oauth_server.revoke_user_app(db, user.id, app_id)
        if revoked is None:
            raise HTTPException(404, "App not found")
        return {"deleted": True, "connections": revoked}

    # -- passkeys ------------------------------------------------------------------------------
    def _passkey(p: Passkey) -> dict:
        return {"id": p.id, "name": p.name, "synced": p.backed_up, "created_at": _iso(p.created_at),
                "last_used_at": _iso(p.last_used_at), "_links": {"self": link(f"{V1}/me/passkeys/{p.id}")}}

    @router.get("/me/passkeys", tags=["account"], response_model=S.PasskeyPage,
                summary="Passkeys that can sign in to this account (add one at POST /api/auth/passkey/register/options)")
    def list_passkeys(request: Request, cursor: str | None = None, limit: int | None = None,
                      user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Passkey).where(Passkey.user_id == user.id)))
        page, nxt = paginate(rows, lambda p: (-p.id,), lambda p: p.id, cursor=cursor, limit=limit)
        return page_body(request, [_passkey(p) for p in page], nxt, len(rows), limit=limit)

    @router.delete("/me/passkeys/{passkey_id}", tags=["account"], summary="Remove a passkey")
    def delete_passkey(passkey_id: Id, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        remove_passkey(db, user.id, passkey_id)
        db.commit()
        return {"deleted": True}

    # -- personal access tokens (agents, scripts, MCP) ------------------------------------------
    def _token(t: AccessToken) -> dict:
        return {"id": t.id, "name": t.name, "prefix": t.prefix, "scopes": t.scopes.split(),
                "created_at": _iso(t.created_at), "expires_at": _iso(t.expires_at), "last_used_at": _iso(t.last_used_at),
                "_links": {"self": link(f"{V1}/me/tokens/{t.id}")}}

    @router.post("/me/tokens", tags=["account"], response_model=S.NewAccessToken, status_code=201,
                 summary="Create a personal access token for your own agents and scripts (shown once)")
    def create_token(request: Request, body: S.AccessTokenIn, user: User = Depends(account_user),
                     db: Session = Depends(get_db)):
        def run() -> dict:
            row, token = tokens.create_pat(db, user, body.name.strip() or "Agent", body.scopes, body.expires_in_days)
            return {**_token(row), "token": token, "mcp_url": f"{settings.base_url}/api/mcp"}

        def already_created(stored: dict):
            # The secret is shown once and never stored, so a retry can't show it again. It gets the
            # token's id instead of a second token: revoke that one and create a new one.
            raise HTTPException(409, f"This token was already created (id {stored['id']}) and its secret is shown "
                                     "only once. Revoke it and create a new one.",
                                headers={"Location": f"{V1}/me/tokens/{stored['id']}"})

        return idempotent(request, db, user, 201, run, redact=lambda answer: {"id": answer["id"]},
                          replay=already_created)

    @router.get("/me/tokens", tags=["account"], response_model=S.AccessTokenPage, summary="Your personal access tokens")
    def list_tokens(request: Request, cursor: str | None = None, limit: int | None = None,
                    user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(AccessToken).where(AccessToken.user_id == user.id)))
        page, nxt = paginate(rows, lambda t: (-t.id,), lambda t: t.id, cursor=cursor, limit=limit)
        return page_body(request, [_token(t) for t in page], nxt, len(rows), limit=limit)

    @router.delete("/me/tokens/{token_id}", tags=["account"], summary="Revoke a personal access token")
    def delete_token(token_id: Id, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        row = db.get(AccessToken, token_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(404, "Token not found")
        db.delete(row)
        db.commit()
        return {"deleted": True}

    # -- collection (own and shared) ----------------------------------------------------------
    @dataclass
    class Ctx:
        db: Session
        owner: User
        hide_costs: bool
        base: str
        own: bool
        owner_name: str | None = None

        def view(self, bucket_id: int | None = None, tag: str | None = None) -> CollectionView:
            return CollectionView(self.db, self.owner, hide_costs=self.hide_costs, bucket_id=bucket_id, tag=tag)

        def bucket(self, bucket_id: int | None) -> int | None:
            """The bucket a request names, if it is the caller's own: buckets are the person's own grouping, not part of
            what a share shows (docs/collections.md), and another person's id is the same 404 as one that does not exist."""
            if bucket_id is None:
                return None
            found = self.own and self.db.scalar(select(Bucket.id).where(Bucket.id == bucket_id, Bucket.user_id == self.owner.id))
            if not found:
                raise HTTPException(404, "Bucket not found")
            return bucket_id

        def scope(self, bucket_id: int | None, tag: str | None) -> tuple[int | None, str | None]:
            """The ``bucket`` and ``tag`` filters of a request, checked: another person's or an unknown bucket id is 404, a malformed
            tag 400, and on a shared collection both are 404 (buckets and tags are the owner's own grouping). An unknown tag is not
            an error: it matches nothing, as on /collection/cards."""
            bucket_id = self.bucket(bucket_id)
            if tag is not None:
                try:
                    tag = card_tags.normalize(tag)
                except card_tags.TagError as exc:
                    raise HTTPException(400, str(exc)) from None
                if not self.own:
                    raise HTTPException(404, "Tag not found")
            return bucket_id, tag

    def own_ctx(db: Session = Depends(get_db), user: User = Depends(current_user)) -> Ctx:
        return Ctx(db, user, False, f"{V1}/collection", True)

    def shared_ctx(share_id: Id, db: Session = Depends(get_db), user: User = Depends(current_user)) -> Ctx:
        share = incoming_share(db, user, share_id, "collection")
        owner = db.get(User, share.owner_id)
        return Ctx(db, owner, not share.show_costs, f"{V1}/shared/{share_id}/collection", False, display_name(owner))

    def collection_routes(ctx_dep) -> APIRouter:
        r = APIRouter(tags=["collection"])

        def card_links(ctx: Ctx, g) -> dict:
            return {"self": link(f"{ctx.base}/cards/{g.id}")}

        def card_out(data: dict | None) -> dict | None:
            if data and settings.twins_url:  # local development: images come from the Scryfall twin
                data = {**data, "image": {k: outbound.browser_url(settings, v) if k in ("small", "normal") and v else v
                                          for k, v in data["image"].items()}}
            return data

        @r.get("", response_model=S.CollectionSummary, summary="Collection summary and links")
        def summary(request: Request, ctx: Ctx = Depends(ctx_dep), bucket: BucketQ = None, tag: TagQ = None):
            bucket_id, tag = ctx.scope(bucket, tag)
            view = ctx.view(bucket_id, tag)
            qs = scope_query(bucket_id, tag)  # the links keep the selection

            def body():
                links = {
                    "self": link(f"{ctx.base}{qs}"), "cards": link(f"{ctx.base}/cards{qs}"),
                    "most_valuable": link(f"{ctx.base}/cards?sort=-value" + qs.replace("?", "&")),
                    "sets": link(f"{ctx.base}/sets{qs}"),
                    "timeline": link(f"{ctx.base}/timeline{qs}"), "history": link(f"{ctx.base}/history{qs}"),
                    "stats": link(f"{ctx.base}/stats{qs}"),
                    "breakdowns": link(f"{ctx.base}/breakdowns{qs}", title="By colour, type, mana value, rarity"),
                    "valuation": link(f"{ctx.base}/valuation{qs}", title="Cumulative value and cost by month"),
                    "names": link(f"{ctx.base}/names{qs}", title="One row per card name"),
                }
                if ctx.own:
                    links |= {"imports": link(f"{V1}/imports"), "export": link(f"{ctx.base}/export.csv"),
                              "exports": link(f"{ctx.base}/exports", title="Export to Moxfield, Archidekt, CSV, text"),
                              "refresh": link(f"{V1}/collection/refresh", title="POST: refresh card data and today's "
                                              "prices from Scryfall, a chunk per call")}
                version = hashlib.sha256(view.version.encode()).hexdigest()[:16]  # changes whenever the data does
                # P&L in SQL, over the copies with a known cost only (vault.analytics)
                pnl = analytics.HIDDEN_PNL if ctx.hide_costs else analytics.pnl(ctx.db, ctx.owner.id, bucket_id, tag)
                return {**view.summary(), **pnl, "owner": ctx.owner_name, "version": version, "_links": links}

            return etag_response(request, view.version, body)

        @r.get("/cards", response_model=S.CardPage, summary="Printings you own, one page at a time")
        def cards(request: Request, q: str | None = None, set: str | None = None, name: str | None = None,
                  finish: str | None = None, condition: str | None = None, sort: str = "name",
                  cursor: str | None = None, limit: int | None = None, ctx: Ctx = Depends(ctx_dep),
                  printing: str | None = Query(None, description="Printing label: Normal, Foil, Etched, … (any case)"),
                  card_type: str | None = Query(None, alias="type", max_length=40,
                                                description=f"Main type, as in the breakdowns and list_card_names: {', '.join(analytics.TYPES)} (any case). "
                                                            "Printings whose card data is not stored yet are left out"),
                  mana_value: str | None = Query(None,
                                                 description=f"Mana value bucket, as in the breakdowns: {', '.join(analytics.MANA_VALUES)}. "
                                                             "Printings whose card data is not stored yet are left out"),
                  bucket: int | None = Query(None, ge=1, le=S.MAX_ID, description="Only the copies in this bucket "
                                             "(GET /collection/buckets); not on a shared collection"),
                  tag: str | None = Query(None, max_length=40, description="Only cards you tagged with this (GET /collection/tags); "
                                          "not on a shared collection")):
            if sort not in SORTS:
                raise HTTPException(400, f"sort must be one of {sorted(SORTS)}")
            if card_type is not None:
                card_type = next((t for t in analytics.TYPES if t.lower() == card_type.strip().lower()), None)
                if card_type is None:
                    raise HTTPException(400, f"type must be one of {', '.join(analytics.TYPES)}")
            if mana_value is not None and mana_value not in analytics.MANA_VALUES:
                raise HTTPException(400, f"mana_value must be one of {', '.join(analytics.MANA_VALUES)}")
            bucket_id, tag = ctx.scope(bucket, tag)
            view = ctx.view(bucket_id)

            def body():
                items = filtered(view, q=q, set_code=set, finish=finish, condition=condition, name=name,
                                 card_type=card_type, mana_value=mana_value)
                items = filtered_printing(items, printing)
                if tag is not None:  # the tag is on the card: every printing of a tagged card matches
                    tagged = card_tags.oracle_ids_for(ctx.db, ctx.owner, tag)
                    printings = {*ctx.db.scalars(select(Card.scryfall_id).where(Card.oracle_id.in_(tagged)))} if tagged else frozenset()  # (`set` is a parameter here)
                    items = [g for g in items if g.scryfall_id in printings]
                page, nxt = paginate(items, SORTS[sort], lambda g: g.id, cursor=cursor, limit=limit)
                cards = view.cards(page)  # card data for the whole page in one query
                mine = card_tags.tags_of(ctx.db, ctx.owner, {c["oracle_id"] for c in cards.values() if c["oracle_id"]}) if ctx.own else None
                out = [{**view.item(g), "card": card_out(cards.get(g.scryfall_id)), "_links": card_links(ctx, g),
                        **({"tags": mine.get((cards.get(g.scryfall_id) or {}).get("oracle_id"), [])} if mine is not None else {})}
                       for g in page]
                return {**page_body(request, out, nxt, len(items), q=q, set=set, name=name, finish=finish,
                                    condition=condition, printing=printing, type=card_type, mana_value=mana_value,
                                    bucket=bucket, tag=tag, sort=None if sort == "name" else sort, limit=limit),
                        "value_total": round(sum(g.value for g in items), 2)}

            return etag_response(request, view.version + (f".{card_tags.stamp(ctx.db, ctx.owner)}" if ctx.own else ""), body)

        @r.get("/cards/{card_id}", response_model=S.CardDetail, summary="One printing, with card data and price history")
        def card(request: Request, card_id: str, ctx: Ctx = Depends(ctx_dep)):
            view = ctx.view()
            g = view.by_id.get(card_id)
            if g is None:
                raise HTTPException(404, "Card not found")

            def body():
                # the rows this printing came from, bounded so one response stays small (copies_total counts all)
                copies = [dict(c, purchase_price=None if ctx.hide_costs else c["purchase_price"]) for c in g.copies[:MAX_COPY_ROWS]]
                links = card_links(ctx, g) | {"collection": link(ctx.base),
                                              "same_card": link(f"{ctx.base}/cards?{urlencode({'name': g.name.split(' // ')[0]})}")}
                card_data = card_out(view.card_data(g))
                if card_data and card_data.get("scryfall_uri"):
                    links["scryfall"] = link(card_data["scryfall_uri"], title="View on Scryfall")
                oracle_id = (view.card_data(g) or {}).get("oracle_id")
                mine = card_tags.tags_of(ctx.db, ctx.owner, {oracle_id}).get(oracle_id, []) if ctx.own and oracle_id else None
                return {**view.item(g), "card": card_data, "price_history": view.price_history(g),
                        "copies": copies, "copies_total": len(g.copies), "_links": links,
                        **({"tags": mine} if mine is not None else {})}

            return etag_response(request, view.version + (f".{card_tags.stamp(ctx.db, ctx.owner)}" if ctx.own else ""), body)

        @r.get("/sets", response_model=S.SetPage, summary="Value by set, with each set's colour mix")
        def sets(request: Request, cursor: str | None = None, limit: int | None = None, ctx: Ctx = Depends(ctx_dep),
                 q: str | None = Query(None, description="Text in the set's code or name"),
                 sort: str = Query("-value", description=f"One of {', '.join(SET_SORTS)}"),
                 bucket: BucketQ = None, tag: TagQ = None):
            if sort not in SET_SORTS:
                raise HTTPException(400, f"sort must be one of {sorted(SET_SORTS)}")
            bucket_id, tag = ctx.scope(bucket, tag)
            view = ctx.view(bucket_id, tag)
            qs = scope_query(bucket_id, tag)
            released = _release_dates(fetch=sort.lstrip("-") == "release")

            def body():
                rows = view.sets()
                if q:
                    needle = q.lower()
                    rows = [s for s in rows if needle in s["code"].lower() or needle in (s["name"] or "").lower()]
                mix = analytics.set_colors(ctx.db, ctx.owner.id, bucket_id, tag)
                empty = {k: 0 for k in analytics.COLORS + (analytics.UNKNOWN,)}
                rows = [{**s, "colors": mix.get(s["code"], empty),
                         "released_at": released.get(_scryfall_set(s["code"]))} for s in rows]
                page, nxt = paginate(rows, SET_SORTS[sort], lambda s: s["code"], cursor=cursor, limit=limit)
                items = [{**s, "_links": {"cards": link(f"{ctx.base}/cards?set={s['code']}" + qs.replace("?", "&"))}} for s in page]
                return page_body(request, items, nxt, len(rows), q=q, sort=None if sort == "-value" else sort,
                                 bucket=bucket, tag=tag, limit=limit)

            return etag_response(request, f"{view.version}.{len(released)}", body)

        @r.get("/timeline", response_model=S.Timeline, summary="Copies acquired per month")
        def timeline(request: Request, ctx: Ctx = Depends(ctx_dep), bucket: BucketQ = None, tag: TagQ = None):
            bucket_id, tag = ctx.scope(bucket, tag)
            view = ctx.view(bucket_id, tag)
            return etag_response(request, view.version, lambda: {
                "months": view.timeline(), "_links": {"self": link(f"{ctx.base}/timeline{scope_query(bucket_id, tag)}")}})

        @r.get("/history", response_model=S.HistoryPage, summary="Daily market value (and cost)")
        def history(request: Request, since: date | None = None, cursor: str | None = None,
                    limit: int | None = None, ctx: Ctx = Depends(ctx_dep), bucket: BucketQ = None, tag: TagQ = None):
            bucket_id, tag = ctx.scope(bucket, tag)

            def body():
                rows = history_days(ctx.db, ctx.owner, since)
                imported = import_days(ctx.db, ctx.owner)
                page, nxt = paginate(rows, lambda v: (v.day.isoformat(),), lambda v: v.user_id, cursor=cursor, limit=limit)
                items = [{"day": v.day.isoformat(), "market": finite(v.market_usd) or 0.0,
                          "cost": None if ctx.hide_costs else finite(v.cost_usd), "copies": v.copies,
                          "priced": v.priced_copies, "imported": v.day in imported} for v in page]
                if bucket_id is not None or tag is not None:
                    # the recorded totals are for the whole inventory: price the selection's copies on each day of this page
                    priced = analytics.scoped_history(ctx.db, ctx.owner.id, [v.day for v in page], bucket_id, tag)
                    zero = {"market": 0.0, "cost": 0.0, "copies": 0, "priced": 0}
                    items = [{**i, **(priced.get(v.day) or zero), "cost": None if ctx.hide_costs else (priced.get(v.day) or zero)["cost"]}
                             for i, v in zip(items, page)]
                return page_body(request, items, nxt, len(rows), since=since and since.isoformat(), bucket=bucket, tag=tag, limit=limit)

            return etag_response(request, sql_version(ctx, bucket_id, tag), body)

        @r.get("/stats", summary="Highlights: most valuable, gains and losses, duplicates (stockpiles)")
        def stats(request: Request, ctx: Ctx = Depends(ctx_dep),
                  limit: int = Query(8, ge=1, le=MAX_STATS, description="How many items in each list"),
                  bucket: BucketQ = None, tag: TagQ = None):
            bucket_id, tag = ctx.scope(bucket, tag)
            view = ctx.view(bucket_id, tag)

            def body():
                data = view.stats(top=limit)
                for key in ("most_valuable", "biggest_gains", "biggest_losses"):
                    for item in data.get(key, []):
                        item["_links"] = {"self": link(f"{ctx.base}/cards/{item['id']}")}
                # stockpiles: how many printings (as /cards groups them) and how much value each name holds
                prints, worth = Counter(), Counter()
                for g in view.groups:
                    prints[g.name] += 1
                    worth[g.name] += g.value
                for item in data["most_copies"]:
                    item |= {"printings": prints[item["name"]], "market_value": round(worth[item["name"]], 2),
                             "_links": {"cards": link(f"{ctx.base}/cards?{urlencode({'name': item['name'].split(' // ')[0]})}")}}
                return {**data, "_links": {"self": link(f"{ctx.base}/stats{scope_query(bucket_id, tag)}")}}

            return etag_response(request, view.version, body)

        # -- analytics, aggregated in Postgres (vault.analytics) ------------------------------
        def sql_version(ctx: Ctx, bucket_id: int | None = None, tag: str | None = None) -> str:
            return view_version(ctx.db, ctx.owner, hide_costs=ctx.hide_costs, bucket_id=bucket_id, tag=tag)

        @r.get("/breakdowns", response_model=S.Breakdowns,
               summary="Copies, printings and value by colour identity, main type, mana value, rarity, colour x type")
        def breakdowns(request: Request, ctx: Ctx = Depends(ctx_dep), bucket: BucketQ = None, tag: TagQ = None):
            bucket_id, tag = ctx.scope(bucket, tag)
            qs = scope_query(bucket_id, tag)
            return etag_response(request, sql_version(ctx, bucket_id, tag), lambda: {
                **analytics.breakdowns(ctx.db, ctx.owner.id, bucket_id, tag),
                "_links": {"self": link(f"{ctx.base}/breakdowns{qs}"), "names": link(f"{ctx.base}/names{qs}")}})

        @r.get("/valuation", response_model=S.Valuation,
               summary="Cumulative market value, cost and gain by purchase month; peak month; last 12 months")
        def valuation(request: Request, ctx: Ctx = Depends(ctx_dep), bucket: BucketQ = None, tag: TagQ = None):
            bucket_id, tag = ctx.scope(bucket, tag)
            qs = scope_query(bucket_id, tag)
            return etag_response(request, sql_version(ctx, bucket_id, tag), lambda: {
                **analytics.valuation(ctx.db, ctx.owner.id, ctx.hide_costs, bucket_id, tag),
                "_links": {"self": link(f"{ctx.base}/valuation{qs}"), "timeline": link(f"{ctx.base}/timeline{qs}"),
                           "history": link(f"{ctx.base}/history{qs}")}})

        @r.get("/names", response_model=S.NamePage,
               summary="One row per card name: copies, value, printings, sets, colour, type (paged)")
        def names(request: Request, ctx: Ctx = Depends(ctx_dep), cursor: str | None = None,
                  limit: int | None = Query(None, description="Page size (top N), at most 500"),
                  sort: str = Query("-value", description=f"One of {', '.join(analytics.NAME_SORTS)}"),
                  colors: str | None = Query(None, description="Comma-separated W,U,B,R,G,M,C: a multicolour "
                                             "card matches any of its colours, and M matches every multicolour card"),
                  type: str | None = Query(None, description="Main type: Creature, Land, …, Other or unknown"),
                  min_value: float | None = Query(None, ge=0, description="Only names worth at least this (USD)"),
                  bucket: BucketQ = None, tag: TagQ = None):
            if sort not in analytics.NAME_SORTS:
                raise HTTPException(400, f"sort must be one of {sorted(analytics.NAME_SORTS)}")
            bucket_id, tag = ctx.scope(bucket, tag)
            wanted = [c.strip().upper() for c in (colors or "").split(",") if c.strip()]
            if any(c not in analytics.COLORS for c in wanted):
                raise HTTPException(400, f"colors must be among {','.join(analytics.COLORS)}")
            size = clamp_limit(limit)
            after = decode_cursor(cursor) if cursor else None
            if after is not None and not isinstance(after, list):
                raise HTTPException(400, "Invalid cursor")

            def body():
                try:
                    items, total, nxt = analytics.names(ctx.db, ctx.owner.id, sort=sort, colors=wanted, type_=type,
                                                        min_value=min_value, after=after, limit=size, bucket_id=bucket_id, tag=tag)
                except analytics.BadCursor:
                    raise HTTPException(400, "Invalid cursor") from None
                for item in items:
                    item["image"] = (card_out({"image": item["image"]}) or {}).get("image") if item["image"] else None
                    item["_links"] = {"cards": link(f"{ctx.base}/cards?{urlencode({'name': item['name'].split(' // ')[0]})}")}
                return page_body(request, items, encode_cursor(nxt) if nxt else None, total, sort=None if sort == "-value" else sort,
                                 colors=colors, type=type, min_value=min_value, bucket=bucket, tag=tag, limit=limit)

            return etag_response(request, sql_version(ctx, bucket_id, tag), body)

        return r

    own = collection_routes(own_ctx)

    def _download(ctx: Ctx, fmt: str, bucket: int | None = None) -> Response:
        f = FORMATS[fmt]
        bucket = ctx.bucket(bucket)
        name = f"vault-collection-{fmt}{'-bucket-' + str(bucket) if bucket else ''}-{date.today().isoformat()}.{f.extension}"
        return Response(export_collection(ctx.db, ctx.owner, fmt, bucket), media_type=f"{f.media_type}; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @own.get("/exports", response_model=S.ExportFormats,
             summary="Formats you can export your collection in, to move it to another app")
    def exports(ctx: Ctx = Depends(own_ctx)) -> dict:
        items = [{"format": f.name, "label": f.label, "description": f.description, "media_type": f.media_type,
                  "extension": f.extension, "reimportable": f.parse is not None,
                  "_links": {"download": link(f"{V1}/collection/export/{f.name}")}} for f in FORMATS.values()]
        return {"items": items, "_links": {"self": link(f"{V1}/collection/exports")}}

    @own.get("/export/{fmt}", summary="Download your collection in one format (see /collection/exports)")
    def export_as(fmt: str, ctx: Ctx = Depends(own_ctx), bucket: int | None = Query(None, ge=1, le=S.MAX_ID,
                  description="Only the copies in this bucket")) -> Response:
        if fmt not in FORMATS:
            raise HTTPException(404, f"Unknown format. Available: {', '.join(FORMATS)}")
        return _download(ctx, fmt, bucket)

    @own.get("/export.csv", summary="Your collection as a Dragon Shield CSV (same as /collection/export/dragonshield)")
    def export_csv(ctx: Ctx = Depends(own_ctx), bucket: int | None = Query(None, ge=1, le=S.MAX_ID,
                   description="Only the copies in this bucket")) -> Response:
        return _download(ctx, "dragonshield", bucket)

    router.include_router(own, prefix="/collection")
    router.include_router(collection_routes(shared_ctx), prefix="/shared/{share_id}/collection")

    # -- imports ------------------------------------------------------------------------------
    def _import(i: Import, undoable_id: int | None = None) -> dict:
        out = {"id": i.id, "filename": i.filename, "source": i.source, "rows": i.rows, "copies": i.copies,
               "changes": i.summary, "kind": i.kind or "import",
               "created_at": _iso(i.created_at), "_links": {"self": link(f"{V1}/imports/{i.id}")}}
        if i.kind in ("assistant", "undo"):
            out |= {"app": i.app, "lines": (i.changes or {}).get("lines")}
        elif (i.changes or {}).get("merge"):
            out["merge"] = i.changes["merge"]  # what the app changed, which Vault edits were kept, the conflicts
        if i.kind == "assistant":  # the web app shows Undo on the entry that can be undone (docs/owned-cards-updates.md, rule 5)
            out |= {"undoable": i.id == undoable_id, "undone": bool((i.changes or {}).get("undone_by"))}
        return out

    @router.post("/imports", tags=["imports"], response_model=S.ImportItem, status_code=201,
                 summary="Upload a collection file: Dragon Shield, Moxfield or generic CSV, detected automatically. "
                         "Applies only what changed in the person's app since the last import and keeps edits made in the "
                         "Vault (conflicts keep the Vault's edit unless answered); records what changed")
    async def create_import(request: Request, file: UploadFile, user: User = Depends(current_user),
                            db: Session = Depends(get_db), options: ImportOptions = Depends(import_options)):
        content = await file.read(MAX_UPLOAD_BYTES + 1)  # enough to reject an oversized file, no more

        def run():
            try:
                return _import(import_collection(db, user, file.filename or "upload.csv", content, options))
            except ImportConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except ImportError_ as exc:
                raise HTTPException(400, str(exc)) from exc

        return idempotent(request, db, user, 201, run)

    @router.post("/imports/preview", tags=["imports"],
                 summary="What uploading this collection file would change, without changing anything")
    async def preview_import(file: UploadFile, user: User = Depends(current_user), db: Session = Depends(get_db),
                             options: ImportOptions = Depends(import_options)) -> dict:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        try:
            return preview_collection_import(db, user, content, options)
        except ImportError_ as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/imports", tags=["imports"], response_model=S.ImportPage)
    def list_imports(request: Request, cursor: str | None = None, limit: int | None = None,
                     user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Import).where(Import.user_id == user.id)))
        page, nxt = paginate(rows, lambda i: (-i.id,), lambda i: i.id, cursor=cursor, limit=limit)
        last = owned_changes.last_undoable(db, user)
        return page_body(request, [_import(i, last.id if last else None) for i in page], nxt, len(rows), limit=limit)

    @router.get("/imports/{import_id}", tags=["imports"], response_model=S.ImportItem)
    def get_import(import_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        imp = db.get(Import, import_id)
        if imp is None or imp.user_id != user.id:
            raise HTTPException(404, "Import not found")
        last = owned_changes.last_undoable(db, user)
        return _import(imp, last.id if last else None)

    # -- decks ----------------------------------------------------------------------------------
    def _parse(text: str) -> decklist.Decklist:
        try:
            return deck_text.parse_text(text)
        except (ValueError, OverflowError) as exc:  # e.g. a quantity with thousands of digits
            raise HTTPException(400, f"The decklist could not be read: {exc}") from exc

    def _coverage(text: str, owned_rows) -> dict:
        deck = _parse(text)
        needed = deck.to_entries()
        lines = delta.coverage(needed, [r.to_collection_entry() for r in owned_rows])
        # Repeats of a card are one line; it names a printing only when every repeat names the same
        # one, so the copies are priced by name rather than all as the first printing.
        printings: dict[str, set] = {}
        for e in needed:
            printings.setdefault(e.name.strip().lower(), set()).add(((e.set_code or "").lower(), (e.collector_number or "").lower()))
        mixed = {k for k, v in printings.items() if len(v) > 1}
        # A card you own none of may still be in the collection under a name written a little
        # differently (accents, punctuation, an Alchemy "A-" prefix): say so instead of only "missing".
        similar: dict[str, Counter] = {}
        if any(not c.have for c in lines):
            for r in owned_rows:
                if r.quantity > 0:  # a row of 0 copies isn't owning the card
                    similar.setdefault(loose_name(r.name), Counter())[r.name] += r.quantity
        sections = {}
        for line in deck.lines:  # where each card sits in the list (a card in two sections keeps its first)
            sections.setdefault(line.name.strip().lower(), line.section)
        return {"cards": [{"name": c.entry.name,
                           "section": sections.get(c.entry.name.strip().lower(), "main"),
                           **({"set": None, "number": None} if c.entry.name.strip().lower() in mixed
                              else {"set": c.entry.set_code, "number": c.entry.collector_number}),
                           "need": c.need, "have": c.have, "missing": c.missing, "status": c.status,
                           "maybe_owned": [] if c.have else [{"name": n, "quantity": q} for n, q in
                                                             sorted(similar.get(loose_name(c.entry.name), {}).items())]}
                          for c in lines],
                "unparsed": deck.unparsed}

    def _source_kind(url: str | None) -> str:
        host = (urlsplit(url).hostname or "").lower() if url else ""
        def is_site(domain: str) -> bool:  # the domain or a subdomain of it: "evilarchidekt.com" is neither (#350)
            return host == domain or host.endswith("." + domain)

        return "archidekt" if is_site("archidekt.com") else "moxfield" if is_site("moxfield.com") else "link" if url else "pasted"

    def _deck_number(found: tuple) -> int:
        """The number of an Archidekt deck link. The parser takes anything ``str.isdigit`` calls a digit (a superscript two
        is one), which ``int`` then refuses: that was a 500 (#350)."""
        number = found[1]
        if not (number.isascii() and number.isdigit()) or len(number) > 12:
            raise HTTPException(400, "That is not an Archidekt deck number (archidekt.com/decks/<number>)")
        return int(number)

    def _deck(d: Deck, coverage: dict | None = None, known: dict | None = None, brief: bool = False) -> dict:
        """A deck answer: what it is at a glance (format, commanders: vault.deck_overview) first, then the list."""
        out = {"id": d.id, "name": d.name, "format": d.format, "overview": deck_overview.overview(d.text, d.format, known),
               "source_url": d.source_url, "source": _source_kind(d.source_url), "source_author": d.source_author,
               "created_at": _iso(d.created_at), "updated_at": _iso(d.updated_at),
               "_links": {"self": link(f"{V1}/decks/{d.id}")}}
        if not brief:
            out["text"] = d.text
        if coverage is not None:
            out["coverage"] = coverage
        credit = deck_overview.archidekt_credit(d)
        if credit:
            out["credit"] = credit
        deck_overview.refresh_note(d, out["overview"])
        return out

    @router.post("/decks/parse", tags=["decks"], response_model=S.ParsedDeck,
                 summary="Parse a pasted decklist (Archidekt, Moxfield, Arena, MTGO formats)")
    def parse_deck(body: S.TextIn, user: User = Depends(current_user)) -> dict:
        deck = _parse(body.text)
        return {"cards": [{"name": l.name, "set": l.set_code or "", "collector_number": l.collector_number or "",
                           "qty": l.quantity, "finish": l.finish.value, "section": l.section,
                           "categories": l.categories} for l in deck.lines],
                "unparsed": deck.unparsed}

    @router.post("/decks/coverage", tags=["decks"], response_model=S.Coverage,
                 summary="Which cards of a decklist you own")
    def deck_coverage(body: S.TextIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        return analytics.price_coverage(db, user.id, _coverage(body.text, user_entries(db, user)))

    BASICS = {"plains", "island", "swamp", "mountain", "forest", "wastes", "snow-covered plains", "snow-covered island",
              "snow-covered swamp", "snow-covered mountain", "snow-covered forest", "snow-covered wastes"}

    @router.get("/decks/overlap", tags=["decks"],
                summary="Cards in more than one saved deck, and whether you own enough copies to build them all at once")
    def deck_overlap(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        decks = list(db.scalars(select(Deck).where(Deck.user_id == user.id).order_by(Deck.name)))
        owned = delta.aggregate([r.to_collection_entry() for r in user_entries(db, user)], delta.BY_CARD)
        uses: dict = {}
        for d in decks:
            try:
                needed = delta.aggregate(deck_text.parse_text(d.text).to_entries(), delta.BY_CARD)
            except (ValueError, OverflowError):
                continue  # an unreadable saved deck is skipped, not an error for the others
            for key, entry in needed.items():
                if entry.name.strip().lower() not in BASICS:
                    uses.setdefault(key, (entry.name, []))[1].append({"deck_id": d.id, "deck": d.name,
                                                                       "quantity": entry.quantity})
        shared = []
        for key, (name, in_decks) in uses.items():
            if len(in_decks) < 2:
                continue
            need, have = sum(u["quantity"] for u in in_decks), owned[key].quantity if key in owned else 0
            shared.append({"name": name, "decks": in_decks, "need_for_all": need, "have": have,
                           "short": max(0, need - have)})
        shared.sort(key=lambda s: (-s["short"], -len(s["decks"]), s["name"].lower()))
        return {"decks_checked": len(decks), "shared_cards": len(shared),
                "short_cards": sum(1 for s in shared if s["short"]), "cards": shared[:200],
                "note": "Basic lands are left out. 'short' is how many more copies you need to have every deck "
                        "built at the same time; any printing you own counts."}

    @router.get("/decks", tags=["decks"], response_model=S.DeckPage)
    def list_decks(request: Request, cursor: str | None = None, limit: int | None = None, summary: bool = False,
                   brief: bool = Query(False, description="Leave each deck's card text out: name, format, commanders, counts"),
                   q: str | None = Query(None, max_length=200, description="Find a deck by words from its name ('sliver swarm'); "
                                         "best match first; when nothing matches, `closest` lists near names"),
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Deck).where(Deck.user_id == user.id)))
        closest: list[str] = []
        if q and q.strip():
            ids, closest = deck_match.search({d.id: d.name for d in rows}, q, limit or 25)
            by_id = {d.id: d for d in rows}
            page, nxt = [by_id[i] for i in ids], None  # ranked by how well the name matches, not by name
        else:
            page, nxt = paginate(rows, lambda d: (d.name.lower(),), lambda d: d.id, cursor=cursor, limit=limit)
        known = deck_overview.identities(db, [c for d in page for c in deck_overview.read(d.text)["commanders"]])
        items = [_deck(d, known=known, brief=brief) for d in page]
        if summary and page:  # each deck against your collection; the collection and prices read once for the page
            owned = user_entries(db, user)
            readable = []
            for item, d in zip(items, page):
                try:
                    readable.append((item, _coverage(d.text, owned)))
                except HTTPException:  # a saved list the parser can no longer read
                    continue
            priced = analytics.price_coverages(db, user.id, [c for _, c in readable], owned_printings=False)
            for (item, _), cov in zip(readable, priced):
                need = sum(c["need"] for c in cov["cards"])
                have = sum(min(c["have"], c["need"]) for c in cov["cards"])
                item["summary"] = {"need": need, "have": have, "missing": need - have,
                                   "missing_cost": cov.get("missing_cost"), "missing_unpriced": cov.get("missing_unpriced")}
        searching = bool(q and q.strip())
        # `total` counts what the request is about: every deck, or, with a name search, the decks that matched (an assistant read
        # "total 2, one listed" as "two decks match, one shown")
        body = page_body(request, items, nxt, len(page) if searching else len(rows), limit=limit,
                         summary="true" if summary else None, q=q or None)
        if searching:
            body["closest"] = closest
        return body

    @router.post("/decks", tags=["decks"], response_model=S.Deck, status_code=201)
    def create_deck(request: Request, body: S.DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not _parse(body.text).lines:
            raise HTTPException(400, "No cards found in the decklist")

        def run():
            deck = Deck(user_id=user.id, name=body.name.strip()[:200] or "Untitled deck", text=body.text,
                        source_url=body.source_url, source_author=body.source_author, format=body.format,
                        source_fetched_at=datetime.now(timezone.utc) if body.source_url else None)
            db.add(deck)
            db.flush()
            deck_versions.record(db, deck, "saved")
            return _deck(deck)

        return idempotent(request, db, user, 201, run)

    LEAN_LINES = 40  # cards shown by ?detail=summary: the dearest of those not fully owned

    def _lean_coverage(covered: dict) -> tuple[dict, dict]:
        """(coverage, summary) of a deck without the per-card detail: counts, and the cards still to get (#232). The full
        list is 94 lines of about 840 bytes for a 100-card deck: 95% of an answer an assistant mostly does not need."""
        lines = covered["cards"]
        todo = sorted((c for c in lines if c["status"] != "owned"),
                      key=lambda c: (-(c.get("missing_cost") or 0), -(c["missing"]), c["name"]))
        keep = ("name", "section", "set", "number", "need", "have", "missing", "status", "unit_price", "price_date", "missing_cost")
        shown = [{k: c[k] for k in keep} for c in todo[:LEAN_LINES]]
        need = sum(c["need"] for c in lines)
        have = sum(min(c["have"], c["need"]) for c in lines)
        coverage = {"cards": shown, "cards_total": len(lines), "fully_owned": len(lines) - len(todo),
                    "shown": (f"the {len(shown)} dearest of the {len(todo)} cards not fully owned" if len(todo) > len(shown)
                              else f"the {len(todo)} cards not fully owned") + "; detail=cards lists every card with its owned printings",
                    "unparsed": covered["unparsed"], "missing_cost": covered.get("missing_cost"),
                    "missing_unpriced": covered.get("missing_unpriced"), "priced_as_of": covered.get("priced_as_of")}
        summary = {"need": need, "have": have, "missing": need - have, "missing_cost": covered.get("missing_cost"),
                   "missing_unpriced": covered.get("missing_unpriced")}
        return coverage, summary

    @router.get("/decks/{deck_id}", tags=["decks"], response_model=S.Deck,
                summary="A saved deck, with coverage (detail=summary: counts and what is still to get, much smaller)")
    def get_deck(deck_id: Id, detail: Literal["cards", "summary"] = Query(
                     "cards", description="cards: every card's ownership with the printings owned (the website); summary: the "
                                          "deck, counts, and the cards not fully owned (the assistants' default)"),
                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        covered = analytics.price_coverage(db, user.id, _coverage(deck.text, user_entries(db, user)))
        day = db.scalar(select(func.max(PriceSnapshot.day)))
        covered["priced_as_of"] = day.isoformat() if day else None  # the prices are Scryfall's, from this day
        out = _deck(deck, covered, deck_overview.identities(db, deck_overview.read(deck.text)["commanders"]))
        if detail == "summary":
            out["coverage"], out["summary"] = _lean_coverage(covered)
        out["last_change"] = deck_versions.last_change(db, deck)
        return out

    @router.get("/decks/{deck_id}/versions", tags=["decks"], response_model=S.DeckVersions,
                summary="A saved deck's versions, newest first, each with what changed from the one before (at most 20 are kept)")
    def list_deck_versions(deck_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        items = deck_versions.versions(db, deck)
        return {"deck_id": deck.id, "keep": deck_versions.KEEP, "total": len(items), "items": items,
                "_links": {"self": link(f"{V1}/decks/{deck.id}/versions")}}

    @router.post("/decks/{deck_id}/seen", tags=["decks"], response_model=S.DeckSeen,
                 summary="The person opened the deck page: record the list they saw if its cards changed, say what changed since "
                         "they last looked, and mark it seen")
    def deck_seen(deck_id: Id, body: S.DeckSeenIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        if body.text is not None and not _parse(body.text).lines:
            raise HTTPException(400, "No cards found in the decklist")
        out = deck_versions.seen(db, deck, body.text)
        db.commit()
        return out

    @router.get("/decks/{deck_id}/versions/{version_id}", tags=["decks"], response_model=S.DeckVersionText,
                summary="One older version of a saved deck: its list as it was")
    def get_deck_version(deck_id: Id, version_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        version = deck_versions.get(db, deck, version_id)
        if version is None:
            raise HTTPException(404, "No such version of this deck")
        return {"id": version.id, "deck_id": deck.id, "created_at": _iso(version.created_at), "source": version.source,
                "text": version.text, "_links": {"self": link(f"{V1}/decks/{deck.id}/versions/{version.id}")}}

    @router.put("/decks/{deck_id}", tags=["decks"], response_model=S.Deck)
    def update_deck(deck_id: Id, body: S.DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        from datetime import datetime, timezone

        deck = owned_deck(db, user, deck_id)
        if not _parse(body.text).lines:
            raise HTTPException(400, "No cards found in the decklist")
        values = {"name": body.name.strip()[:200] or deck.name, "text": body.text,
                  "updated_at": datetime.now(timezone.utc)}
        if "source_url" in body.model_fields_set:  # omitted: keep it (null clears it)
            values["source_url"] = body.source_url
            values["source_fetched_at"] = values["updated_at"] if body.source_url else None
        # No `source_url`: the caller (an assistant's update_deck) changed the list without reading the link, so the time the list was
        # last taken from it stays as it was (#322). The web app always sends the link with its save; refresh_deck stamps its own.
        if "format" in body.model_fields_set:  # likewise
            values["format"] = body.format
        if "source_author" in body.model_fields_set:
            values["source_author"] = body.source_author
        elif "source_url" in values:
            # A new source without its author: the old one isn't its author. Decided in the UPDATE against the
            # row's link at that moment (SET reads the old row), so a concurrent change of link can't leave an
            # author next to a link that isn't theirs.
            values["source_author"] = case((Deck.source_url.is_not_distinct_from(body.source_url), Deck.source_author),
                                           else_=None)
        # One UPDATE of the row as it is now: a deck deleted meanwhile is simply not found.
        done = db.execute(update(Deck).where(Deck.id == deck.id, Deck.user_id == user.id).values(**values))
        if done.rowcount != 1:
            db.rollback()
            raise HTTPException(404, "Deck not found")
        db.commit()
        db.refresh(deck)
        deck_versions.record(db, deck, "edited")
        db.commit()
        return _deck(deck)

    @router.post("/decks/{deck_id}/source-author", tags=["decks"], response_model=S.AuthorRecorded,
                 summary="Record the source's author on a copy saved before authors were kept")
    def record_deck_author(deck_id: Id, body: S.DeckAuthorIn, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
        # Only the author, only if the deck still has that link and no author yet: an edit made meanwhile
        # (another tab, another device) is never overwritten, and the deck's updated time doesn't change.
        # One locked read of the caller's row: a deck that isn't theirs, or was deleted meanwhile, is 404,
        # and nothing can change or delete it between this check and the write.
        deck = db.execute(select(Deck).where(Deck.id == deck_id, Deck.user_id == user.id).with_for_update()
                          .execution_options(populate_existing=True)).scalar_one_or_none()
        if deck is None:
            raise HTTPException(404, "Deck not found")
        recorded = deck.source_url == body.source_url and deck.source_author is None
        if recorded:
            deck.source_author = body.source_author
        db.commit()
        return {**_deck(deck), "recorded": recorded}  # the deck as it is now, with its links

    @router.delete("/decks/{deck_id}", tags=["decks"])
    def delete_deck(deck_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        db.execute(delete(Share).where(Share.deck_id == deck.id))
        db.delete(deck)
        db.commit()
        return {"deleted": True}

    @router.get("/archidekt/decks/{deck_id}", tags=["decks"],
                summary="A public Archidekt deck, read for you (fetched server-side; repeat reads within 10 minutes come from a cache)")
    def archidekt_deck(deck_id: Id, refresh: bool = False,
                       detail: Literal["summary", "cards"] = Query(default="summary", description="cards adds every card with its printing (the web app); summary stays small (assistants)"),
                       user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        """The deck as the Vault saves it: name, author, format, commander(s), counts and the list with its sections, and
        Archidekt's own bracket tag. Archidekt's raw answer is 300 KB for 100 cards, mostly other shops' prices per card
        (Card Kingdom, Cardmarket, ...), which the Vault does not pass on: it quotes only Scryfall's dated prices (#219)."""
        raw = archidekt_cache.read(db, deck_id, fetch_archidekt, refresh=refresh)
        parsed = deck_import.to_decklist(raw)
        known = deck_overview.identities(db, deck_overview.read(parsed["text"])["commanders"])
        url = deck_import.canonical_url(deck_id)
        extra = {"cards": deck_import.card_lines(raw)} if detail == "cards" else {}
        return {**extra,
                "deck": {"id": deck_id, "name": parsed["name"], "author": parsed["author"], "url": url, "source": "archidekt",
                         "overview": deck_overview.overview(parsed["text"], parsed["format"], known)},
                "archidekt_bracket": raw.get("edhBracket"), "counts": parsed["counts"], "text": parsed["text"],
                "credit": {"source": "Archidekt", "url": url, "author": parsed["author"],
                           "fetched_at": (raw.get("vault_cache") or {}).get("fetched_at"),
                           "notice": "Deck list from Archidekt" + (f" by {parsed['author']}" if parsed["author"] else "")
                                     + ". The deck is theirs, not the Vault's."},
                "note": "Archidekt's per-card shop prices are not passed on: the Vault quotes only Scryfall's dated prices.",
                "vault_cache": raw.get("vault_cache")}

    def fetch_archidekt(deck: int) -> dict:
        """One read of a public deck. Bounded, because the function has a time limit and a thread and a person's request are
        held while it runs: the shared client would wait 30 s after a 429 and try three more times (more than a minute)."""
        try:
            with ArchidektClient(client=httpx.Client(transport=transport, timeout=httpx.Timeout(10, connect=5),
                                                     follow_redirects=True)) as client:
                client.max_retries, client.rate_limit_backoff, client.max_retry_wait = 1, 3.0, 5.0
                return client.get_deck(deck).raw
        except ApiError as exc:
            if exc.status_code == 404:
                raise HTTPException(404, str(exc)) from exc
            if exc.status_code == 429:
                raise HTTPException(503, "Archidekt is limiting requests right now, so the deck cannot be read. Try again in a minute.",
                                    headers={"Retry-After": "60"}) from exc
            raise HTTPException(502, "Archidekt could not be reached or did not answer properly. Try again shortly. " + str(exc)[:120]) from exc

    @router.post("/decks/import-link", tags=["decks"],
                 summary="Save a public Archidekt deck from its link: the server reads it and keeps its sections; the same link never "
                         "duplicates")
    def import_deck_from_link(request: Request, body: S.ImportLinkIn, user: User = Depends(current_user),
                              db: Session = Depends(get_db)) -> dict:
        found = decklist.parse_url(body.url)
        if found is None or found[0] != "archidekt":
            raise HTTPException(400, "Only Archidekt deck links can be fetched (archidekt.com/decks/<number>). For another site, "
                                     "export the list as text and save it with save_deck.")
        deck_id = _deck_number(found)

        def run():
            parsed = deck_import.to_decklist(archidekt_cache.read(db, deck_id, fetch_archidekt,
                                                                  refresh=body.update and not body.confirm))
            if not parsed["text"] or not _parse(parsed["text"]).lines:
                raise HTTPException(400, "That Archidekt deck has no cards")
            url = deck_import.canonical_url(deck_id)
            same = next((d for d in db.scalars(select(Deck).where(Deck.user_id == user.id, Deck.source_url.is_not(None)))
                         if (decklist.parse_url(d.source_url) or ("", ""))[1:] == (str(deck_id),)), None)
            if same is not None and not body.update:
                return {"created": False, "updated": False, "deck": _deck(same), "counts": parsed["counts"],
                        "note": "This deck is already saved. With update true, the answer shows what Archidekt's current list changes."}
            if same is not None and not body.confirm:  # an update shows what changes first (#217)
                return {"created": False, "updated": False, **_refresh_preview(same, parsed)}
            if same is not None:
                if body.fingerprint != deck_refresh.fingerprint(parsed["text"]):
                    raise HTTPException(409, "Archidekt's list is not the one previewed (or no fingerprint was given): preview again")
                same.text, same.updated_at = parsed["text"], datetime.now(timezone.utc)
                same.source_fetched_at = same.updated_at
                same.format = parsed["format"] or same.format
                same.source_url, same.source_author = url, parsed["author"] or same.source_author
                if body.name:
                    same.name = body.name.strip()[:200] or same.name
                db.flush()
                deck_versions.record(db, same, "refreshed")
                return {"created": False, "updated": True, "deck": _deck(same), "counts": parsed["counts"]}
            deck = Deck(user_id=user.id, name=(body.name or parsed["name"] or "Archidekt deck").strip()[:200], text=parsed["text"],
                        source_url=url, source_author=parsed["author"], format=parsed["format"],
                        source_fetched_at=datetime.now(timezone.utc))
            db.add(deck)
            db.flush()
            deck_versions.record(db, deck, "imported")
            return {"created": True, "updated": False, "deck": _deck(deck), "counts": parsed["counts"]}

        return idempotent(request, db, user, 201, run)

    # -- refreshing a saved deck from its stored link (vault.deck_refresh, #217) --------------------
    def _refresh_preview(deck: Deck, parsed: dict) -> dict:
        changes = deck_refresh.diff(deck.text, parsed["text"])
        return {"deck": _deck(deck, brief=True), "source": {"url": deck.source_url, "name": parsed["name"], "author": parsed["author"]},
                "changes": changes, "summary": deck_refresh.summary(changes), "unchanged": not changes,
                "fingerprint": deck_refresh.fingerprint(parsed["text"]),
                "note": "Nothing has changed yet." if changes else "The saved list already matches the source."}

    @router.post("/decks/{deck_id}/refresh", tags=["decks"],
                 summary="Compare a saved deck with its stored Archidekt link; replace the list only with confirm and the preview's fingerprint")
    def refresh_deck(request: Request, deck_id: Id, body: S.DeckRefreshIn, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        if not deck.source_url:
            raise HTTPException(400, "This deck has no stored link to refresh from. Paste the current list to update it.")
        found = decklist.parse_url(deck.source_url)
        if found is None or found[0] != "archidekt":
            raise HTTPException(400, "Only Archidekt links can be read by the Vault. For Moxfield and other sites (their terms do "
                                     "not allow automated reading), export the current list there and paste it to update the deck.")
        # the preview asks Archidekt again (unless the copy is under a minute old); the confirm uses that same copy
        parsed = deck_import.to_decklist(archidekt_cache.read(db, _deck_number(found), fetch_archidekt, refresh=not body.confirm))
        if not parsed["text"] or not _parse(parsed["text"]).lines:
            raise HTTPException(400, "The deck on Archidekt has no cards now: nothing was changed")
        if not body.confirm:
            return _refresh_preview(deck, parsed)
        if body.fingerprint != deck_refresh.fingerprint(parsed["text"]):
            raise HTTPException(409, "The list on Archidekt is not the one previewed (or no fingerprint was given): preview again")

        def run():
            changes = deck_refresh.diff(deck.text, parsed["text"])
            deck.text, deck.updated_at = parsed["text"], datetime.now(timezone.utc)
            deck.source_fetched_at = deck.updated_at
            deck.format = parsed["format"] or deck.format
            deck.source_author = parsed["author"] or deck.source_author
            db.flush()
            deck_versions.record(db, deck, "refreshed")
            return {"refreshed": True, "deck": _deck(deck, brief=True), "changes": changes,
                    "summary": deck_refresh.summary(changes)}

        return idempotent(request, db, user, 200, run)
    # -- the expert council for connectors (vault.experts, #220) ------------------------------
    @router.get("/council", tags=["decks"],
                summary="The expert council's panel for a format and goal, with every member's brief and the chair's procedure")
    def council_brief(format: str | None = Query(None, max_length=40, description="commander, limited, pauper, standard, "
                                                 "pioneer, two-headed-giant ... (read from the deck when deck_id is given)"),
                      goal: str | None = Query(None, max_length=300, description="Tune, check, explain, synergies, budget ..."),
                      deck_id: int | None = Query(None, ge=1, le=2**31 - 1),
                      team_format: str | None = Query(None, max_length=40, description="Two-Headed Giant: the format the team plays"),
                      budget: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        note = None
        if deck_id is not None and not format:
            deck = owned_deck(db, user, deck_id)
            seen = deck_overview.overview(deck.text, deck.format)
            format = seen["format"]
            note = (f"Format from the deck '{deck.name}': {format or 'unknown'} ({seen['format_from'] or 'not set'})"
                    + (f"; commander(s): {', '.join(seen['commanders'])}" if seen["commanders"] else ""))
        return experts.council(format, goal, team_format, budget, note)

    @router.get("/experts", tags=["decks"], summary="The council's experts: who they are")
    def list_experts(user: User = Depends(current_user)) -> dict:
        return {"experts": experts.roster()}

    @router.get("/experts/{expert}", tags=["decks"], summary="One expert's brief, to answer as that expert")
    def get_expert(expert: str = Path(max_length=60, pattern=r"^[a-z0-9-]+$"), user: User = Depends(current_user)) -> dict:
        found = experts.brief(expert)
        if found is None:
            raise HTTPException(404, "No such expert: list them with GET /experts")
        return found

    # -- card catalog (Scryfall data, served by the Vault) ---------------------------------------
    catalog = Catalog(transport, rewrite_image=(lambda url: outbound.browser_url(settings, url)) if settings.twins_url else None)

    # -- assistant edits to owned cards (vault.owned_changes, docs/owned-cards-updates.md) ---------
    def lookup_printing(db: Session, set_code: str, number: str):
        """A printing the Vault does not know yet, looked up at Scryfall by set and number (and kept, as lookups are)."""
        try:
            catalog.lookup(db, [{"set": set_code, "collector_number": number}])
        except (ApiError, httpx.HTTPError):
            return None
        return db.scalar(select(Card).where(func.lower(Card.set_code) == set_code.lower(),
                                            func.lower(Card.collector_number) == number.lower()))

    def changes_answer(out: dict) -> dict:
        out.pop("_resolved", None)
        return out

    @router.get("/collection/printings", tags=["collection"],
                summary="The printings of one card you own, most copies first, each with its Scryfall image")
    def owned_printings(name: str = Query(min_length=1, max_length=300), user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
        return owned_changes.owned_printings(db, user, name)

    @router.post("/collection/changes/preview", tags=["collection"],
                 summary="Preview small edits to the cards owned (add, remove, set): changes nothing; returns a confirmation")
    def preview_owned_changes(request: Request, body: S.OwnedChangesIn, user: User = Depends(current_user),
                              db: Session = Depends(get_db)) -> dict:
        per_user(request, "owned changes preview", user.id, 30, db)
        try:
            return changes_answer(owned_changes.preview(db, user, [line.model_dump() for line in body.lines],
                                                        settings.session_secret, lambda s, n: lookup_printing(db, s, n),
                                                        live=catalog.live_printings))
        except owned_changes.ChangeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/collection/changes/apply", tags=["collection"], status_code=201,
                 summary="Apply exactly the previewed edits, with the preview's confirmation, after the person said yes")
    def apply_owned_changes(request: Request, body: S.OwnedChangesApplyIn, user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
        per_user(request, "owned changes", user.id, 10, db)
        label = tokens.app_label(db, getattr(request.state, "bearer", None))

        def run():
            try:
                imp = owned_changes.apply(db, user, [line.model_dump() for line in body.lines], body.confirmation,
                                          settings.session_secret, lambda s, n: lookup_printing(db, s, n), label)
            except owned_changes.ChangeError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"applied": True, "change_set": imp.id, "summary": imp.summary, "app": imp.app,
                    "value_change_usd": imp.changes["value_change_usd"],
                    "undo": "undo_owned_cards_update reverts it until the collection changes again"}

        return idempotent(request, db, user, 201, run)

    @router.post("/collection/changes/undo", tags=["collection"],
                 summary="Undo the last assistant change set: without a confirmation, preview; with it, apply")
    def undo_owned_changes(request: Request, body: S.OwnedUndoIn, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
        per_user(request, "owned changes", user.id, 10, db)
        imp = owned_changes.last_undoable(db, user)
        if imp is None:
            raise HTTPException(409, "Nothing to undo: only the last change made through an assistant, and only until the "
                                     "collection changes again")
        lines = owned_changes.undo_lines(imp)
        lookup = lambda s, n: lookup_printing(db, s, n)  # noqa: E731
        try:
            if not body.confirmation:
                return {**changes_answer(owned_changes.preview(db, user, lines, settings.session_secret, lookup, undo=True)),
                        "undoes": imp.id,
                        "to_apply": "After the person says yes, call undo_owned_cards_update again with this confirmation "
                                    "(not confirm_owned_cards_update)."}
            done = owned_changes.apply(db, user, lines, body.confirmation, settings.session_secret, lookup,
                                       tokens.app_label(db, getattr(request.state, "bearer", None)), undo_of=imp)
        except owned_changes.ChangeError as exc:
            raise HTTPException(409, str(exc)) from exc
        db.commit()
        return {"undone": imp.id, "change_set": done.id, "summary": done.summary}

    @router.post("/cards/lookup", tags=["cards"], response_model=S.CardLookup, response_model_by_alias=True,
                 summary="Card data, images and prices for up to 75 printings (Scryfall's collection lookup, via the Vault)")
    def lookup_cards(body: S.CardLookupIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        idents = [i.model_dump(exclude_none=True) for i in body.identifiers]  # each complete (CardIdentifier)
        return {**catalog.lookup(db, idents, refresh=body.refresh), "_links": {"self": link(f"{V1}/cards/lookup")}}

    @router.get("/catalog/sets", tags=["cards"], response_model=S.SetCatalog, response_model_by_alias=True,
                summary="Every Magic set: code, name, icon, release date (refreshed daily from Scryfall), paged by code")
    def catalog_sets(request: Request, response: Response, cursor: str | None = None, limit: int | None = None,
                     user: User = Depends(current_user)) -> dict:
        """Signed-in people only (#62): this is Scryfall's set list served as it is, and the Vault serves no Scryfall data
        to strangers (an anonymous copy would be the proxy their terms forbid). The website reads it after sign-in."""
        try:
            items = catalog.sets()
        except (ApiError, httpx.HTTPError) as exc:
            raise HTTPException(503, "Scryfall's set list is unavailable right now", headers={"Retry-After": "60"}) from exc
        page, nxt = paginate(items, lambda s: (s["code"],), lambda s: s["code"], cursor=cursor, limit=limit)
        response.headers["Cache-Control"] = "private, max-age=86400"  # the browser may keep it a day; no shared cache may
        # Dragon Shield's own set codes (e.g. gk2_orzhov) and the Scryfall set each stands for.
        return {**page_body(request, page, nxt, len(items), limit=limit), "aliases": set_alias_map(), "alias_prefixes": list(SET_ALIAS_PREFIXES)}

    def _release_dates(fetch: bool) -> dict[str, str]:
        """Release date by Scryfall set code. Only ``fetch`` (sort=release) may call Scryfall (once a
        day per process); otherwise the dates are given only when the set list is already cached."""
        try:
            items = catalog.sets() if fetch else catalog.cached_sets()
        except (ApiError, httpx.HTTPError) as exc:
            raise HTTPException(503, "Scryfall's set list is unavailable right now", headers={"Retry-After": "60"}) from exc
        return {s["code"]: s["released_at"] for s in items if s.get("released_at")}

    @router.post("/collection/refresh", tags=["collection"], response_model=S.RefreshProgress,
                 summary="Refresh your printings' card data and today's prices from Scryfall, one chunk per call",
                 description="Fetches up to 300 printings per call (Scryfall's collection lookup in batches of 75, "
                             "through the server's rate-limited client), then recomputes today's collection value, so "
                             "the collection's `version` changes. Call again with the answer's `cursor` until "
                             "`remaining` is 0. Printings that already have today's price are skipped unless "
                             "`force`. Limited per user (REFRESH_RATE_LIMIT a minute); needs the write scope.")
    def refresh_collection(request: Request, body: S.RefreshIn | None = None, user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
        body = body or S.RefreshIn()
        per_user(request, "refresh", user.id, settings.refresh_rate_limit, db)
        today = date.today()
        state = analytics.refresh_state(db, user.id, today, body.cursor, body.force)
        processed = not_found = 0
        unavailable, cursor = False, body.cursor
        ids = state["ids"]
        for i in range(0, len(ids), 75):  # Scryfall's own limit per /cards/collection request
            batch = ids[i:i + 75]
            answer = catalog.lookup(db, [{"id": sid} for sid in batch], refresh=True)
            if answer["unavailable"]:
                unavailable = True
                break
            processed += len(batch)
            not_found += len(answer["not_found"])
            cursor = batch[-1]
        if processed:
            compute_values(db, today, user.id)  # today's value at the new prices (commits)
        elif unavailable:
            raise HTTPException(503, "Scryfall didn't answer. Try again shortly.", headers={"Retry-After": "30"})
        after = analytics.refresh_state(db, user.id, today, cursor, body.force, limit=0)
        version = hashlib.sha256(view_version(db, user).encode()).hexdigest()[:16]
        prices_as_of = db.scalar(select(func.max(PriceSnapshot.day)))
        return {"done": after["total"] - after["remaining"], "total": after["total"], "remaining": after["remaining"],
                "processed": processed, "not_found": not_found, "unmatched_rows": after["unmatched"],
                "cursor": cursor if after["remaining"] else None, "unavailable": unavailable,
                "prices_as_of": prices_as_of.isoformat() if prices_as_of else None, "version": version,
                "_links": {"self": link(f"{V1}/collection/refresh"), "collection": link(f"{V1}/collection")}}

    # -- sharing ------------------------------------------------------------------------------
    @router.post("/shares", tags=["sharing"], response_model=S.Invite, status_code=201,
                 summary="Create a one-time invite link for your collection or a deck")
    def create_share(request: Request, body: S.ShareIn, user: User = Depends(account_user), db: Session = Depends(get_db)):
        def invite(share: Share, token: str) -> dict:
            return {"id": share.id, "url": f"{settings.base_url}/?invite={token}", "expires_at": _iso(share.expires_at),
                    "_links": {"self": link(f"{V1}/shares/{share.id}")}}

        # The stored answer keeps only the invite id, never the link: a retry derives the same link again.
        secret = settings.session_secret
        return idempotent(request, db, user, 201, lambda: invite(*create_invite(db, user, body.kind, body.deck_id,
                                                                                body.show_costs, secret)),
                          redact=lambda answer: {"id": answer["id"]},
                          replay=lambda stored: invite(*invite_again(db, user, stored["id"], secret)))

    @router.get("/shares", tags=["sharing"], response_model=S.SharePage, response_model_by_alias=True,
                summary="What you have shared, and with whom")
    def list_shares(request: Request, cursor: str | None = None, limit: int | None = None,
                    user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Share).where(Share.owner_id == user.id)))
        page, nxt = paginate(rows, lambda s: (-s.id,), lambda s: s.id, cursor=cursor, limit=limit)
        items = []
        for s in page:
            deck = db.get(Deck, s.deck_id) if s.deck_id else None
            items.append({
                "id": s.id, "kind": s.kind, "deck_id": s.deck_id, "deck_name": deck.name if deck else None,
                "show_costs": s.show_costs, "status": "active" if s.grantee_id else "pending",
                "with": display_name(db.get(User, s.grantee_id)) if s.grantee_id else None,
                "created_at": _iso(s.created_at),
                "expires_at": _iso(s.expires_at) if not s.grantee_id else None,
                "_links": {"self": link(f"{V1}/shares/{s.id}")},
            })
        return page_body(request, items, nxt, len(rows), limit=limit)

    @router.delete("/shares/{share_id}", tags=["sharing"], summary="Revoke (owner) or leave (recipient)")
    def remove_share(share_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        share = db.get(Share, share_id)
        if share is None or user.id not in (share.owner_id, share.grantee_id):
            raise HTTPException(404, "Not found")
        db.delete(share)
        db.commit()
        return {"deleted": True}

    @router.post("/shares/accept", tags=["sharing"], summary="Accept an invite link's token (send an "
                 "Idempotency-Key to retry safely: the link itself works once)")
    def accept_share(request: Request, body: S.AcceptIn, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
        def run() -> dict:
            share = accept_invite(db, user, body.token)
            deck = db.get(Deck, share.deck_id) if share.deck_id else None
            target = f"{V1}/shared/{share.id}/" + ("collection" if share.kind == "collection" else "deck")
            return {"id": share.id, "kind": share.kind, "from": display_name(db.get(User, share.owner_id)),
                    "deck_name": deck.name if deck else None, "_links": {"shared": link(target)}}

        return idempotent(request, db, user, 200, run)

    @router.get("/shared", tags=["sharing"], response_model=S.SharedPage, summary="What others have shared with you")
    def shared_with_me(request: Request, cursor: str | None = None, limit: int | None = None,
                       user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Share).where(Share.grantee_id == user.id)))
        page, nxt = paginate(rows, lambda s: (-s.id,), lambda s: s.id, cursor=cursor, limit=limit)
        items = []
        for s in page:
            deck = db.get(Deck, s.deck_id) if s.deck_id else None
            target = f"{V1}/shared/{s.id}/" + ("collection" if s.kind == "collection" else "deck")
            items.append({"id": s.id, "kind": s.kind, "from": display_name(db.get(User, s.owner_id)),
                          "deck_name": deck.name if deck else None, "show_costs": s.show_costs,
                          "_links": {"self": link(target)}})
        return page_body(request, items, nxt, len(rows), limit=limit)

    @router.get("/shared/{share_id}/deck", tags=["sharing"], response_model=S.Deck,
                summary="A deck someone shared, with coverage against your collection")
    def shared_deck(share_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        share = incoming_share(db, user, share_id, "deck")
        deck = db.get(Deck, share.deck_id)
        out = _deck(deck, analytics.price_coverage(db, user.id, _coverage(deck.text, user_entries(db, user))))
        out["_links"] = {"self": link(f"{V1}/shared/{share_id}/deck")}
        out["from"] = display_name(db.get(User, share.owner_id))
        return out

    return router

"""The Vault API, version 1 (``/api/v1``), for the web app and native apps.

Start at ``GET /api/v1`` and follow ``_links``. Authentication: the web app's session
cookie, or ``Authorization: Bearer <access_token>`` for native apps (see ``/auth``).
Every list is cursor-paginated; no response grows with the size of a collection
beyond one page (max 500 items).

Tenant isolation: queries are scoped to the signed-in user; other users' data is
reachable only through shares they granted, and ids that aren't yours answer 404.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from mtg_toolkits import decklist, delta
from mtg_toolkits.archidekt import ArchidektClient
from mtg_toolkits.http import ApiError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import outbound, tokens
from ..auth import Profile, find_or_create
from ..collection_view import SORTS, CollectionView, filtered, history_days
from ..importer import ImportError_, export_dragonshield, import_dragonshield, user_entries
from ..models import AccessToken, ApiSession, Deck, Import, Share, User
from ..native import NativeTokenError, NativeVerifier, ProviderUnavailable
from ..privacy import export_archive, purge_user
from ..sharing import accept_invite, create_invite, display_name, incoming_share, owned_deck
from . import schemas as S
from .hal import etag_response, link, page_body, paginate
from .idempotency import idempotent

V1 = "/api/v1"
DELETE_CONFIRMATION = "DELETE"


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
                 summary="Sign in with a native Apple or Google ID token")
    async def native_sign_in(provider: str, body: S.NativeSignIn, db: Session = Depends(get_db),
                             current: User | None = Depends(optional_user)) -> dict:
        try:
            claims = await verifier.verify(provider, body.id_token, body.nonce)
        except NativeTokenError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ProviderUnavailable as exc:
            raise HTTPException(503, f"{exc}. Try again shortly.", headers={"Retry-After": "30"}) from exc
        name = body.name if provider == "apple" else claims.get("name")
        user = find_or_create(db, Profile(provider, str(claims["sub"]), claims.get("email"), name), current)
        return tokens.issue(db, user, "app", body.device_name)

    @router.post("/auth/token", tags=["auth"], response_model=S.TokenResponse,
                 summary="Redeem a browser sign-in code (PKCE) or rotate a refresh token")
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
                       "tokens": link(f"{V1}/me/tokens", title="Personal access tokens for agents and scripts"),
                       "export": link(f"{V1}/me/export", title="Download all my data (ZIP)"),
                       "collection": link(f"{V1}/collection")},
        }

    @router.get("/me", tags=["account"], response_model=S.Me)
    def me(user: User = Depends(current_user)) -> dict:
        return _me(user)

    @router.patch("/me", tags=["account"], response_model=S.Me)
    def update_me(body: S.ProfileUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
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
    def end_session(session_id: int, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        s = db.get(ApiSession, session_id)
        if s is None or s.user_id != user.id:
            raise HTTPException(404, "Session not found")
        db.delete(s)
        db.commit()
        return {"deleted": True}

    # -- personal access tokens (agents, scripts, MCP) ------------------------------------------
    def _token(t: AccessToken) -> dict:
        return {"id": t.id, "name": t.name, "prefix": t.prefix, "scopes": t.scopes.split(),
                "created_at": _iso(t.created_at), "expires_at": _iso(t.expires_at), "last_used_at": _iso(t.last_used_at),
                "_links": {"self": link(f"{V1}/me/tokens/{t.id}")}}

    @router.post("/me/tokens", tags=["account"], response_model=S.NewAccessToken, status_code=201,
                 summary="Create a personal access token for your own agents and scripts (shown once)")
    def create_token(body: S.AccessTokenIn, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        row, token = tokens.create_pat(db, user, body.name.strip() or "Agent", body.scopes, body.expires_in_days)
        return {**_token(row), "token": token, "mcp_url": f"{settings.base_url}/api/mcp"}

    @router.get("/me/tokens", tags=["account"], response_model=S.AccessTokenPage, summary="Your personal access tokens")
    def list_tokens(request: Request, cursor: str | None = None, limit: int | None = None,
                    user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(AccessToken).where(AccessToken.user_id == user.id)))
        page, nxt = paginate(rows, lambda t: (-t.id,), lambda t: t.id, cursor=cursor, limit=limit)
        return page_body(request, [_token(t) for t in page], nxt, len(rows), limit=limit)

    @router.delete("/me/tokens/{token_id}", tags=["account"], summary="Revoke a personal access token")
    def delete_token(token_id: int, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
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

        def view(self) -> CollectionView:
            return CollectionView(self.db, self.owner, hide_costs=self.hide_costs)

    def own_ctx(db: Session = Depends(get_db), user: User = Depends(current_user)) -> Ctx:
        return Ctx(db, user, False, f"{V1}/collection", True)

    def shared_ctx(share_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> Ctx:
        share = incoming_share(db, user, share_id, "collection")
        owner = db.get(User, share.owner_id)
        return Ctx(db, owner, not share.show_costs, f"{V1}/shared/{share_id}/collection", False, display_name(owner))

    def collection_routes(ctx_dep) -> APIRouter:
        r = APIRouter(tags=["collection"])

        def card_links(ctx: Ctx, g) -> dict:
            return {"self": link(f"{ctx.base}/cards/{g.id}")}

        @r.get("", response_model=S.CollectionSummary, summary="Collection summary and links")
        def summary(request: Request, ctx: Ctx = Depends(ctx_dep)):
            view = ctx.view()

            def body():
                links = {
                    "self": link(ctx.base), "cards": link(f"{ctx.base}/cards"),
                    "most_valuable": link(f"{ctx.base}/cards?sort=-value"), "sets": link(f"{ctx.base}/sets"),
                    "timeline": link(f"{ctx.base}/timeline"), "history": link(f"{ctx.base}/history"),
                    "stats": link(f"{ctx.base}/stats"),
                }
                if ctx.own:
                    links |= {"imports": link(f"{V1}/imports"), "export": link(f"{ctx.base}/export.csv")}
                version = hashlib.sha256(view.version.encode()).hexdigest()[:16]  # changes whenever the data does
                return {**view.summary(), "owner": ctx.owner_name, "version": version, "_links": links}

            return etag_response(request, view.version, body)

        @r.get("/cards", response_model=S.CardPage, summary="Printings you own, one page at a time")
        def cards(request: Request, q: str | None = None, set: str | None = None, name: str | None = None,
                  finish: str | None = None, condition: str | None = None, sort: str = "name",
                  cursor: str | None = None, limit: int | None = None, ctx: Ctx = Depends(ctx_dep)):
            if sort not in SORTS:
                raise HTTPException(400, f"sort must be one of {sorted(SORTS)}")
            view = ctx.view()

            def body():
                items = filtered(view, q=q, set_code=set, finish=finish, condition=condition, name=name)
                page, nxt = paginate(items, SORTS[sort], lambda g: g.id, cursor=cursor, limit=limit)
                out = [{**view.item(g), "_links": card_links(ctx, g)} for g in page]
                return page_body(request, out, nxt, len(items), q=q, set=set, name=name, finish=finish,
                                 condition=condition, sort=None if sort == "name" else sort, limit=limit)

            return etag_response(request, view.version, body)

        @r.get("/cards/{card_id}", response_model=S.CardDetail, summary="One printing, with card data and price history")
        def card(request: Request, card_id: str, ctx: Ctx = Depends(ctx_dep)):
            view = ctx.view()
            g = view.by_id.get(card_id)
            if g is None:
                raise HTTPException(404, "Card not found")

            def body():
                copies = [dict(c, purchase_price=None if ctx.hide_costs else c["purchase_price"]) for c in g.copies]
                links = card_links(ctx, g) | {"collection": link(ctx.base),
                                              "same_card": link(f"{ctx.base}/cards?{urlencode({'name': g.name.split(' // ')[0]})}")}
                card_data = view.card_data(g)
                if card_data and settings.twins_url:  # local development: images come from the Scryfall twin
                    card_data["image"] = {k: outbound.browser_url(settings, v) if k in ("small", "normal") and v else v
                                          for k, v in card_data["image"].items()}
                if card_data and card_data.get("scryfall_uri"):
                    links["scryfall"] = link(card_data["scryfall_uri"], title="View on Scryfall")
                return {**view.item(g), "card": card_data, "price_history": view.price_history(g),
                        "copies": copies, "_links": links}

            return etag_response(request, view.version, body)

        @r.get("/sets", response_model=S.SetPage, summary="Value by set")
        def sets(request: Request, cursor: str | None = None, limit: int | None = None, ctx: Ctx = Depends(ctx_dep)):
            view = ctx.view()

            def body():
                rows = view.sets()
                page, nxt = paginate(rows, lambda s: (-s["market_value"],), lambda s: s["code"], cursor=cursor, limit=limit)
                items = [{**s, "_links": {"cards": link(f"{ctx.base}/cards?set={s['code']}")}} for s in page]
                return page_body(request, items, nxt, len(rows), limit=limit)

            return etag_response(request, view.version, body)

        @r.get("/timeline", response_model=S.Timeline, summary="Copies acquired per month")
        def timeline(request: Request, ctx: Ctx = Depends(ctx_dep)):
            view = ctx.view()
            return etag_response(request, view.version,
                                 lambda: {"months": view.timeline(), "_links": {"self": link(f"{ctx.base}/timeline")}})

        @r.get("/history", response_model=S.HistoryPage, summary="Daily market value (and cost)")
        def history(request: Request, since: date | None = None, cursor: str | None = None,
                    limit: int | None = None, ctx: Ctx = Depends(ctx_dep)):
            view = ctx.view()

            def body():
                rows = history_days(ctx.db, ctx.owner, since)
                page, nxt = paginate(rows, lambda v: (v.day.isoformat(),), lambda v: v.user_id, cursor=cursor, limit=limit)
                items = [{"day": v.day.isoformat(), "market": v.market_usd,
                          "cost": None if ctx.hide_costs else v.cost_usd, "copies": v.copies,
                          "priced": v.priced_copies} for v in page]
                return page_body(request, items, nxt, len(rows), since=since and since.isoformat(), limit=limit)

            return etag_response(request, view.version, body)

        @r.get("/stats", summary="Highlights: most valuable, gains and losses, duplicates")
        def stats(request: Request, ctx: Ctx = Depends(ctx_dep)):
            view = ctx.view()

            def body():
                data = view.stats()
                for key in ("most_valuable", "biggest_gains", "biggest_losses"):
                    for item in data.get(key, []):
                        item["_links"] = {"self": link(f"{ctx.base}/cards/{item['id']}")}
                return {**data, "_links": {"self": link(f"{ctx.base}/stats")}}

            return etag_response(request, view.version, body)

        return r

    own = collection_routes(own_ctx)

    @own.get("/export.csv", summary="Your collection as a Dragon Shield CSV (re-importable)")
    def export_csv(ctx: Ctx = Depends(own_ctx)) -> Response:
        return Response(export_dragonshield(ctx.db, ctx.owner), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="vault-export.csv"'})

    router.include_router(own, prefix="/collection")
    router.include_router(collection_routes(shared_ctx), prefix="/shared/{share_id}/collection")

    # -- imports ------------------------------------------------------------------------------
    def _import(i: Import) -> dict:
        return {"id": i.id, "filename": i.filename, "rows": i.rows, "copies": i.copies, "changes": i.summary,
                "created_at": _iso(i.created_at), "_links": {"self": link(f"{V1}/imports/{i.id}")}}

    @router.post("/imports", tags=["imports"], response_model=S.ImportItem, status_code=201,
                 summary="Upload a Dragon Shield CSV export (replaces the collection, records what changed)")
    async def create_import(request: Request, file: UploadFile, user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
        content = await file.read()

        def run():
            try:
                return _import(import_dragonshield(db, user, file.filename or "upload.csv", content))
            except ImportError_ as exc:
                raise HTTPException(400, str(exc)) from exc

        return idempotent(request, db, user, 201, run)

    @router.get("/imports", tags=["imports"], response_model=S.ImportPage)
    def list_imports(request: Request, cursor: str | None = None, limit: int | None = None,
                     user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Import).where(Import.user_id == user.id)))
        page, nxt = paginate(rows, lambda i: (-i.id,), lambda i: i.id, cursor=cursor, limit=limit)
        return page_body(request, [_import(i) for i in page], nxt, len(rows), limit=limit)

    @router.get("/imports/{import_id}", tags=["imports"], response_model=S.ImportItem)
    def get_import(import_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        imp = db.get(Import, import_id)
        if imp is None or imp.user_id != user.id:
            raise HTTPException(404, "Import not found")
        return _import(imp)

    # -- decks ----------------------------------------------------------------------------------
    def _coverage(text: str, owned_rows) -> dict:
        deck = decklist.parse_text(text)
        lines = delta.coverage(deck.to_entries(), [r.to_collection_entry() for r in owned_rows])
        return {"cards": [{"name": c.entry.name, "set": c.entry.set_code, "number": c.entry.collector_number,
                           "need": c.need, "have": c.have, "missing": c.missing, "status": c.status} for c in lines],
                "unparsed": deck.unparsed}

    def _deck(d: Deck, coverage: dict | None = None) -> dict:
        out = {"id": d.id, "name": d.name, "text": d.text, "source_url": d.source_url,
               "created_at": _iso(d.created_at), "updated_at": _iso(d.updated_at),
               "_links": {"self": link(f"{V1}/decks/{d.id}")}}
        if coverage is not None:
            out["coverage"] = coverage
        return out

    @router.post("/decks/parse", tags=["decks"], response_model=S.ParsedDeck,
                 summary="Parse a pasted decklist (Archidekt, Moxfield, Arena, MTGO formats)")
    def parse_deck(body: S.TextIn, user: User = Depends(current_user)) -> dict:
        deck = decklist.parse_text(body.text)
        return {"cards": [{"name": l.name, "set": l.set_code or "", "collector_number": l.collector_number or "",
                           "qty": l.quantity, "finish": l.finish.value, "section": l.section,
                           "categories": l.categories} for l in deck.lines],
                "unparsed": deck.unparsed}

    @router.post("/decks/coverage", tags=["decks"], response_model=S.Coverage,
                 summary="Which cards of a decklist you own")
    def deck_coverage(body: S.TextIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        return _coverage(body.text, user_entries(db, user))

    @router.get("/decks", tags=["decks"], response_model=S.DeckPage)
    def list_decks(request: Request, cursor: str | None = None, limit: int | None = None,
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Deck).where(Deck.user_id == user.id)))
        page, nxt = paginate(rows, lambda d: (d.name.lower(),), lambda d: d.id, cursor=cursor, limit=limit)
        return page_body(request, [_deck(d) for d in page], nxt, len(rows), limit=limit)

    @router.post("/decks", tags=["decks"], response_model=S.Deck, status_code=201)
    def create_deck(request: Request, body: S.DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not decklist.parse_text(body.text).lines:
            raise HTTPException(400, "No cards found in the decklist")

        def run():
            deck = Deck(user_id=user.id, name=body.name.strip()[:200] or "Untitled deck", text=body.text,
                        source_url=body.source_url)
            db.add(deck)
            db.flush()
            return _deck(deck)

        return idempotent(request, db, user, 201, run)

    @router.get("/decks/{deck_id}", tags=["decks"], response_model=S.Deck, summary="A saved deck, with coverage")
    def get_deck(deck_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        return _deck(deck, _coverage(deck.text, user_entries(db, user)))

    @router.put("/decks/{deck_id}", tags=["decks"], response_model=S.Deck)
    def update_deck(deck_id: int, body: S.DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        from datetime import datetime, timezone

        deck = owned_deck(db, user, deck_id)
        deck.name, deck.text, deck.source_url = body.name.strip()[:200] or deck.name, body.text, body.source_url
        deck.updated_at = datetime.now(timezone.utc)
        db.commit()
        return _deck(deck)

    @router.delete("/decks/{deck_id}", tags=["decks"])
    def delete_deck(deck_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        db.execute(delete(Share).where(Share.deck_id == deck.id))
        db.delete(deck)
        db.commit()
        return {"deleted": True}

    @router.get("/archidekt/decks/{deck_id}", tags=["decks"], summary="A public Archidekt deck (fetched server-side)")
    def archidekt_deck(deck_id: int, user: User = Depends(current_user)) -> dict:
        try:
            with ArchidektClient(client=httpx.Client(transport=transport, timeout=30, follow_redirects=True)) as client:
                return client.get_deck(deck_id).raw
        except ApiError as exc:
            raise HTTPException(exc.status_code if exc.status_code == 404 else 502, str(exc)) from exc

    # -- sharing ------------------------------------------------------------------------------
    @router.post("/shares", tags=["sharing"], response_model=S.Invite, status_code=201,
                 summary="Create a one-time invite link for your collection or a deck")
    def create_share(request: Request, body: S.ShareIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        def run():
            share, token = create_invite(db, user, body.kind, body.deck_id, body.show_costs)
            return {"id": share.id, "url": f"{settings.base_url}/?invite={token}", "expires_at": _iso(share.expires_at),
                    "_links": {"self": link(f"{V1}/shares/{share.id}")}}

        return idempotent(request, db, user, 201, run)

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
    def remove_share(share_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        share = db.get(Share, share_id)
        if share is None or user.id not in (share.owner_id, share.grantee_id):
            raise HTTPException(404, "Not found")
        db.delete(share)
        db.commit()
        return {"deleted": True}

    @router.post("/shares/accept", tags=["sharing"], summary="Accept an invite link's token")
    def accept_share(body: S.AcceptIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        share = accept_invite(db, user, body.token)
        deck = db.get(Deck, share.deck_id) if share.deck_id else None
        target = f"{V1}/shared/{share.id}/" + ("collection" if share.kind == "collection" else "deck")
        return {"id": share.id, "kind": share.kind, "from": display_name(db.get(User, share.owner_id)),
                "deck_name": deck.name if deck else None, "_links": {"shared": link(target)}}

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
    def shared_deck(share_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        share = incoming_share(db, user, share_id, "deck")
        deck = db.get(Deck, share.deck_id)
        out = _deck(deck, _coverage(deck.text, user_entries(db, user)))
        out["_links"] = {"self": link(f"{V1}/shared/{share_id}/deck")}
        out["from"] = display_name(db.get(User, share.owner_id))
        return out

    return router

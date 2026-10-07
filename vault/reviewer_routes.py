"""Reviewer sign-in and the page that documents it (issue #239). Every route answers 404 unless REVIEWER_PASSPHRASE is set,
so turning the variable off removes the way in at once. See :mod:`vault.reviewer` for what the account is."""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import reviewer
from .config import Settings
from .ratelimit import limited

log = logging.getLogger("vault.reviewer")


class ReviewerLogin(BaseModel):
    passphrase: str = Field(min_length=1, max_length=200)


def build_router(settings: Settings, get_db, sign_in) -> APIRouter:
    router = APIRouter(tags=["reviewers"], include_in_schema=False)

    def enabled() -> None:
        if not settings.reviewer_passphrase:
            raise HTTPException(404)

    @router.post("/api/auth/reviewer-login", dependencies=[Depends(enabled), *limited("reviewer-login", verify=True)])
    def reviewer_login(body: ReviewerLogin, request: Request, db: Session = Depends(get_db)) -> dict:
        if not reviewer.passphrase_ok(body.passphrase, settings.reviewer_passphrase):
            log.warning("reviewer sign-in refused")  # never the passphrase, nor what was tried
            raise HTTPException(401, "That passphrase is not right")
        user = reviewer.seed(db)  # the demo account and its data, created on first use
        sign_in(db, request, reviewer.profile(), link=False)
        log.info("reviewer sign-in")
        return {"id": user.id, "account": reviewer.EMAIL}

    @router.get("/reviewers", dependencies=[Depends(enabled)], response_class=HTMLResponse)
    def reviewers_page() -> HTMLResponse:
        return HTMLResponse(page(settings.base_url), headers={"Cache-Control": "no-store"})

    return router


def _case(i: int, c: dict) -> str:
    tools = ", ".join(f"<code>{html.escape(t)}</code>" for t in c["tools"]) or "none"
    return (f"<li><strong>{html.escape(c['name'])}.</strong> <em>“{html.escape(c['prompt'])}”</em><br>"
            f"Tools: {tools}.<br>Expected: {html.escape(c['expect'])}</li>")


def page(base_url: str) -> str:
    positive = "".join(_case(i, c) for i, c in enumerate(reviewer.POSITIVE))
    negative = "".join(_case(i, c) for i, c in enumerate(reviewer.NEGATIVE))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reviewer guide · The Vault</title><style>body{{font:16px/1.6 system-ui,sans-serif;max-width:720px;margin:32px auto;padding:0 16px}}
code{{background:#8881;padding:1px 4px;border-radius:3px}}li{{margin-bottom:12px}}</style></head><body>
<h1>Reviewer guide</h1>
<p>For the Claude and ChatGPT directory reviews. The Vault is a free fan tool; see the <a href="{html.escape(base_url)}/terms.html">terms</a>,
<a href="{html.escape(base_url)}/privacy.html">privacy notice</a> and <a href="{html.escape(base_url)}/support.html">support</a> pages.</p>
<h2>The demo account</h2>
<p>The account is <code>{html.escape(reviewer.EMAIL)}</code>. It holds only made-up data: about 150 copies of well-known cards and two decks,
<em>Sliver Swarm (demo)</em> (a five-colour Commander deck led by Sliver Overlord, partly owned) and <em>Pauper Burn (demo)</em> (fully owned).
There is no second factor, e-mail or SMS step. Connecting an app opens the Vault's sign-in page, which has a <strong>Reviewer sign-in</strong>
box: enter the passphrase you were given in the review portal, then choose what the app may do. The account can only see its own data.</p>
<h2>Five requests that should work</h2><ol>{positive}</ol>
<h2>Three requests that must be refused or answered honestly</h2><ol>{negative}</ol>
<p>Prices are Scryfall's, dated, and never a store's price today. The Vault never contacts stores or fills carts.</p>
<p>The Vault is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.</p>
</body></html>"""

"""Twins of the four sign-in providers: Google, Microsoft (Entra, "common"), Apple and Facebook.

Each is stateful and holds the things the real provider knows:
- registered apps (client ids, secrets, redirect URIs)
- user accounts
- issued codes and tokens
- signing keys, which can be rotated
- which apps a user has already authorised (Apple sends the name only the first time)

Each validates what the real one validates:
- unknown clients and redirect URI mismatches get an error page, not a redirect
- ``form_post`` is required when Apple is asked for name or email
- a code works once, and only with the redirect URI and PKCE verifier it was issued for
- client secrets, including Apple's ES256 client-secret JWT (verified against the registered key)

A person can sign in through a consent page (server mode); tests call :meth:`approve`.
Hosts, paths, claims and error shapes follow the providers' documentation. The
conformance tests in ``tests/conformance`` compare them with the real services every night.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit, parse_qs

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import ECKey, RSAKey

from .base import Request, Twin, html_response, json_response, redirect

MS_PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


@dataclass
class Account:
    sub: str
    email: str | None = None
    name: str | None = None
    email_verified: bool = True
    hide_email: bool = False  # Apple "Hide My Email"
    tenant: str = MS_PERSONAL_TENANT  # Microsoft
    share_email: bool = True  # Facebook: the user may decline the email permission

    @property
    def first_name(self) -> str | None:
        return self.name.split(" ", 1)[0] if self.name else None

    @property
    def last_name(self) -> str | None:
        return self.name.split(" ", 1)[1] if self.name and " " in self.name else None


@dataclass
class App:
    client_id: str
    client_secret: str | None = None
    redirect_uris: set[str] = field(default_factory=set)
    # Apple: the key that signs the client-secret JWT
    team_id: str | None = None
    key_id: str | None = None
    public_key: str | None = None
    optional_email_claim: bool = False  # Microsoft: "email" is an optional claim, off by default


@dataclass
class Grant:
    app: App
    account: Account
    redirect_uri: str
    scope: set[str]
    nonce: str | None
    code_challenge: str | None
    code_challenge_method: str | None
    expires_at: float


@dataclass
class Callback:
    """Where the provider sends the browser back to, and how."""

    url: str
    method: str = "GET"  # "POST" for Apple's form_post
    data: dict[str, str] = field(default_factory=dict)

    @property
    def params(self) -> dict[str, str]:
        return self.data if self.method == "POST" else {k: v[0] for k, v in parse_qs(urlsplit(self.url).query).items()}


class IdentityTwin(Twin):
    """Shared machinery. Subclasses set hosts, endpoints and claims."""

    authorize_path: str
    authorize_host: str
    code_ttl = 600
    key_params = {"use": "sig", "alg": "RS256"}  # what the JWKS says about each key

    def __init__(self):
        super().__init__()
        self.apps: dict[str, App] = {}
        self.accounts: dict[str, Account] = {}
        self.grants: dict[str, Grant] = {}
        self.used_codes: set[str] = set()
        self.access_tokens: dict[str, tuple[Account, set[str]]] = {}
        self.authorised: set[tuple[str, str]] = set()  # (client_id, sub) pairs that consented before
        self.keys: list[RSAKey] = []
        self.rotate_keys(keep_old=False)
        self.route("GET", self.authorize_host, self.authorize_path, self._authorize_page)
        self.route("POST", self.authorize_host, self.authorize_path, self._authorize_submit)

    # -- state ---------------------------------------------------------------------------
    def register_app(self, client_id: str, client_secret: str | None = None, redirect_uris=(), **kwargs) -> App:
        app = App(client_id, client_secret, set(redirect_uris), **kwargs)
        self.apps[client_id] = app
        return app

    def add_account(self, sub: str, email: str | None = None, name: str | None = None, **kwargs) -> Account:
        account = self.accounts.get(sub) or Account(sub)
        account.email, account.name = email or account.email, name or account.name
        for k, v in kwargs.items():
            setattr(account, k, v)
        self.accounts[sub] = account
        return account

    def rotate_keys(self, keep_old: bool = True) -> RSAKey:
        """Start signing with a new key. ``keep_old=False`` also withdraws the old ones from JWKS."""
        key = RSAKey.generate_key(2048, parameters={"kid": f"{self.name}-{secrets.token_hex(4)}", **self.key_params})
        self.keys = [key] + (self.keys if keep_old else [])
        return key

    def jwks(self) -> dict:
        return {"keys": [k.as_dict(private=False) for k in self.keys]}

    def reset(self) -> None:
        super().reset()
        self.grants.clear()
        self.used_codes.clear()
        self.access_tokens.clear()
        self.authorised.clear()

    def state(self) -> dict:
        return super().state() | {
            "apps": sorted(self.apps), "accounts": [a.__dict__ for a in self.accounts.values()],
            "keys": [k.kid for k in self.keys],
        }

    # -- the authorisation step ------------------------------------------------------------
    def _check_request(self, q: dict[str, str]) -> tuple[App | None, str | None]:
        """Validate an authorisation request. Errors the provider shows on its own page
        (unknown client, bad redirect URI) come back as a message, never as a redirect."""
        app = self.apps.get(q.get("client_id", ""))
        if app is None:
            return None, "invalid_client: The OAuth client was not found."
        if q.get("redirect_uri") not in app.redirect_uris:
            return None, f"redirect_uri_mismatch: {q.get('redirect_uri')} is not registered for this app."
        if q.get("response_type") != "code":
            return None, "unsupported_response_type"
        return app, self._extra_checks(q)

    def _extra_checks(self, q: dict[str, str]) -> str | None:
        return None

    def approve(self, location: str, account: Account | str | None = None, *, sub: str | None = None,
                email: str | None = None, name: str | None = None, claims: dict | None = None) -> Callback:
        """What happens when the user signs in and consents on the provider's page.
        ``location`` is where the app redirected the browser. ``claims`` overrides ID-token
        claims (to forge a bad token)."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        app, problem = self._check_request(q)
        if app is None or problem:
            raise AssertionError(f"{self.name} refused the authorisation request: {problem}")
        if isinstance(account, str):
            account = self.accounts.get(account) or self.add_account(account, email, name)
        elif account is None:
            account = self.add_account(sub or secrets.token_hex(6), email, name)
        return self._grant(app, account, q, claims or {})

    def deny(self, location: str) -> Callback:
        """The user presses Cancel."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        return self._callback(q, self._denied_params())

    def _denied_params(self) -> dict[str, str]:
        return {"error": "access_denied"}

    def _grant(self, app: App, account: Account, q: dict[str, str], claims: dict) -> Callback:
        code = secrets.token_urlsafe(24)
        self.grants[code] = Grant(app, account, q["redirect_uri"], set(q.get("scope", "").split()), q.get("nonce"),
                                  q.get("code_challenge"), q.get("code_challenge_method"), time.time() + self.code_ttl)
        self.grants[code].extra_claims = claims  # type: ignore[attr-defined]
        params = {"code": code, **self._callback_extras(app, account, q)}
        self.authorised.add((app.client_id, account.sub))
        return self._callback(q, params)

    def _callback_extras(self, app: App, account: Account, q: dict[str, str]) -> dict[str, str]:
        return {}

    def _callback(self, q: dict[str, str], params: dict[str, str]) -> Callback:
        if "state" in q:
            params = {**params, "state": q["state"]}
        if q.get("response_mode") == "form_post":
            return Callback(q["redirect_uri"], "POST", params)
        sep = "&" if "?" in q["redirect_uri"] else "?"
        return Callback(f"{q['redirect_uri']}{sep}{urlencode(params)}")

    def _authorize_page(self, req: Request) -> httpx.Response:
        app, problem = self._check_request(req.query)
        if app is None or problem:
            return html_response(self._page(f"<h2>Something went wrong</h2><p class=err>{html.escape(problem or '')}</p>"), 400)
        hidden = "".join(f'<input type=hidden name="q_{html.escape(k)}" value="{html.escape(v)}">' for k, v in req.query.items())
        accounts = "".join(
            f'<button name=account value="{html.escape(a.sub)}">{html.escape(a.name or a.sub)}'
            f'<small>{html.escape(a.email or "no e-mail")}</small></button>'
            for a in self.accounts.values()
        )
        body = f"""
<h2>Sign in with {self.label}</h2>
<p>to continue to <b>{html.escape(app.client_id)}</b></p>
<form method=post>{hidden}
  <div class=accounts>{accounts}</div>
  <fieldset><legend>Use another account</legend>
    <input name=new_email placeholder="e-mail" autocomplete=off>
    <input name=new_name placeholder="name" autocomplete=off>
    <button name=account value="__new__">Continue</button>
  </fieldset>
  <button name=decision value=cancel class=cancel>Cancel</button>
</form>"""
        return html_response(self._page(body))

    def _authorize_submit(self, req: Request) -> httpx.Response:
        q = {k[2:]: v for k, v in req.form.items() if k.startswith("q_")}
        if req.form.get("decision") == "cancel":
            cb = self._callback(q, self._denied_params())
        else:
            app, problem = self._check_request(q)
            if app is None or problem:
                return html_response(self._page(f"<p class=err>{html.escape(problem or '')}</p>"), 400)
            sub = req.form.get("account", "")
            if sub == "__new__":
                email = req.form.get("new_email") or None
                account = self.add_account(hashlib.sha256((email or secrets.token_hex(4)).encode()).hexdigest()[:16],
                                           email, req.form.get("new_name") or None)
            else:
                account = self.accounts[sub]
            cb = self._grant(app, account, q, {})
        if cb.method == "POST":  # the provider's page posts the answer to the app
            fields = "".join(f'<input type=hidden name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in cb.data.items())
            return html_response(f'<form method=post action="{html.escape(cb.url)}">{fields}</form>'
                                 "<script>document.forms[0].submit()</script>")
        return redirect(cb.url)

    label = "Provider"
    colour = "#444"

    def _page(self, body: str) -> str:
        return f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>{self.label} (twin)</title>
<style>body{{font:15px system-ui;max-width:420px;margin:40px auto;padding:0 16px}}
.banner{{background:{self.colour};color:#fff;padding:6px 10px;border-radius:6px;font-size:12px}}
button{{display:block;width:100%;margin:6px 0;padding:10px;text-align:left;border:1px solid #ccc;background:#fff;border-radius:6px;cursor:pointer}}
button small{{display:block;color:#666}} input{{width:100%;margin:4px 0;padding:8px;box-sizing:border-box}}
.cancel{{text-align:center;color:#a00}} .err{{color:#a00}} fieldset{{border:1px solid #ddd;border-radius:6px}}</style>
<div class=banner>Digital twin of {self.label}: not the real service</div>{body}"""

    # -- tokens --------------------------------------------------------------------------
    def _client_ok(self, app: App, req: Request) -> bool:
        secret = req.form.get("client_secret")
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            if cid != app.client_id:
                return False
        return secret is not None and secret == app.client_secret

    def redeem(self, req: Request) -> tuple[Grant | None, httpx.Response | None]:
        """Common authorization_code checks. Returns the grant or the error response."""
        if req.value("grant_type") != "authorization_code":
            return None, self.token_error("unsupported_grant_type", "Only authorization_code is supported by the twin")
        code = req.value("code") or ""
        if code in self.used_codes:
            return None, self.token_error("invalid_grant", "The code has already been used")
        grant = self.grants.pop(code, None)
        if grant is None:
            return None, self.token_error("invalid_grant", "Unknown or expired code")
        self.used_codes.add(code)
        if req.value("client_id") not in (None, grant.app.client_id):
            return None, self.token_error("invalid_client", "The code was issued to another client")
        if not self._client_ok(grant.app, req):
            return None, self.token_error("invalid_client", "Client authentication failed", 401)
        if req.value("redirect_uri") != grant.redirect_uri:
            return None, self.token_error("invalid_grant", "redirect_uri does not match the authorisation request")
        if grant.expires_at < time.time():
            return None, self.token_error("invalid_grant", "The code has expired")
        if grant.code_challenge:
            verifier = req.value("code_verifier") or ""
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            expected = digest if grant.code_challenge_method == "S256" else verifier
            if expected != grant.code_challenge:
                return None, self.token_error("invalid_grant", "PKCE verification failed")
        return grant, None

    def token_error(self, error: str, description: str, status: int = 400) -> httpx.Response:
        return json_response(status, {"error": error, "error_description": description})

    def _issue(self, grant: Grant) -> dict:
        access = secrets.token_urlsafe(32)
        self.access_tokens[access] = (grant.account, grant.scope)
        body = {"access_token": access, "token_type": "Bearer", "expires_in": 3599}
        if "openid" in grant.scope or self.always_id_token:
            claims = self.id_claims(grant.app.client_id, grant.account, grant.nonce, grant.scope, grant.app)
            claims.update(getattr(grant, "extra_claims", {}))
            body["id_token"] = self.sign(claims)
        return body

    always_id_token = False

    def sign(self, claims: dict, key: RSAKey | None = None) -> str:
        key = key or self.keys[0]
        return jwt.encode({"alg": "RS256", "kid": key.kid, "typ": "JWT"}, claims, key)

    def id_claims(self, aud: str, account: Account, nonce: str | None, scope: set[str], app: App | None) -> dict:
        raise NotImplementedError

    def native_id_token(self, aud: str, account: Account | str, nonce: str | None = None, *,
                        key: RSAKey | None = None, **overrides) -> str:
        """The ID token a native SDK (Sign in with Apple, Google Sign-In for iOS) hands the app.
        ``nonce`` goes into the token as given (Apple apps pass SHA-256 of their raw nonce)."""
        if isinstance(account, str):
            account = self.accounts.get(account) or self.add_account(account)
        claims = self.id_claims(aud, account, nonce, {"openid", "email", "name", "profile"}, None)
        claims.update(overrides)
        return self.sign(claims, key)


def _now() -> int:
    return int(time.time())


class GoogleTwin(IdentityTwin):
    name, label, colour = "google", "Google", "#1a73e8"
    hosts = ("accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com", "openidconnect.googleapis.com")
    authorize_host, authorize_path = "accounts.google.com", "/o/oauth2/v2/auth"
    issuer = "https://accounts.google.com"

    def __init__(self):
        super().__init__()
        self.route("GET", "accounts.google.com", "/.well-known/openid-configuration", lambda r: json_response(200, self.discovery()))
        self.route("GET", "www.googleapis.com", "/oauth2/v3/certs", lambda r: json_response(200, self.jwks()))
        self.route("POST", "oauth2.googleapis.com", "/token", self._token)
        self.route("GET", "openidconnect.googleapis.com", "/v1/userinfo", self._userinfo)

    def discovery(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "device_authorization_endpoint": "https://oauth2.googleapis.com/device/code",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
            "revocation_endpoint": "https://oauth2.googleapis.com/revoke",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "response_types_supported": ["code", "token", "id_token", "code token", "code id_token",
                                         "token id_token", "code token id_token", "none"],
            "response_modes_supported": ["query", "fragment", "form_post"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "claims_supported": ["aud", "email", "email_verified", "exp", "family_name", "given_name", "iat",
                                 "iss", "name", "picture", "sub"],
            "code_challenge_methods_supported": ["plain", "S256"],
            "grant_types_supported": ["authorization_code", "refresh_token",
                                      "urn:ietf:params:oauth:grant-type:device_code",
                                      "urn:ietf:params:oauth:grant-type:jwt-bearer"],
        }

    def _callback_extras(self, app, account, q):
        return {"scope": " ".join(sorted(set(q.get("scope", "").split()))), "authuser": "0", "prompt": "consent"}

    def id_claims(self, aud, account, nonce, scope, app):
        now = _now()
        claims = {"iss": self.issuer, "azp": aud, "aud": aud, "sub": account.sub, "iat": now, "exp": now + 3600}
        if "email" in scope and account.email:
            claims |= {"email": account.email, "email_verified": account.email_verified}
        if "profile" in scope and account.name:
            claims |= {"name": account.name, "given_name": account.first_name, "family_name": account.last_name}
        if nonce:
            claims["nonce"] = nonce
        return claims

    def _token(self, req: Request) -> httpx.Response:
        grant, err = self.redeem(req)
        if err:
            return err
        return json_response(200, self._issue(grant) | {"scope": " ".join(sorted(grant.scope))})

    def _userinfo(self, req: Request) -> httpx.Response:
        token = req.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.access_tokens:
            return json_response(401, {"error": "invalid_request", "error_description": "Invalid Credentials"})
        account, scope = self.access_tokens[token]
        claims = self.id_claims("", account, None, scope, None)
        return json_response(200, {k: v for k, v in claims.items() if k not in ("iss", "aud", "azp", "iat", "exp")})


class MicrosoftTwin(IdentityTwin):
    """Microsoft identity platform v2.0 with the ``common`` authority (personal and work accounts)."""

    name, label, colour = "microsoft", "Microsoft", "#2f2f2f"
    key_params = {"use": "sig"}  # Microsoft's JWKS has no "alg" (conformance-checked)
    hosts = ("login.microsoftonline.com", "graph.microsoft.com")
    authorize_host, authorize_path = "login.microsoftonline.com", "/common/oauth2/v2.0/authorize"

    def __init__(self):
        super().__init__()
        base = "login.microsoftonline.com"
        self.route("GET", base, "/common/v2.0/.well-known/openid-configuration", lambda r: json_response(200, self.discovery()))
        self.route("GET", base, "/common/discovery/v2.0/keys", lambda r: json_response(200, self.jwks()))
        self.route("POST", base, "/common/oauth2/v2.0/token", self._token)

    def discovery(self) -> dict:
        b = "https://login.microsoftonline.com"
        return {
            "token_endpoint": f"{b}/common/oauth2/v2.0/token",
            "token_endpoint_auth_methods_supported": ["client_secret_post", "private_key_jwt", "client_secret_basic"],
            "jwks_uri": f"{b}/common/discovery/v2.0/keys",
            "response_modes_supported": ["query", "fragment", "form_post"],
            "subject_types_supported": ["pairwise"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "response_types_supported": ["code", "id_token", "code id_token", "id_token token"],
            "scopes_supported": ["openid", "profile", "email", "offline_access"],
            "issuer": f"{b}/{{tenantid}}/v2.0",
            "request_uri_parameter_supported": False,
            "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
            "authorization_endpoint": f"{b}/common/oauth2/v2.0/authorize",
            "device_authorization_endpoint": f"{b}/common/oauth2/v2.0/devicecode",
            "http_logout_supported": True,
            "frontchannel_logout_supported": True,
            "end_session_endpoint": f"{b}/common/oauth2/v2.0/logout",
            "claims_supported": ["sub", "iss", "cloud_instance_name", "cloud_instance_host_name", "cloud_graph_host_name",
                                 "msgraph_host", "aud", "exp", "iat", "auth_time", "acr", "nonce", "preferred_username",
                                 "name", "tid", "ver", "at_hash", "c_hash", "email"],
            "kerberos_endpoint": f"{b}/common/kerberos",
            "tenant_region_scope": None,
            "cloud_instance_name": "microsoftonline.com",
            "cloud_graph_host_name": "graph.windows.net",
            "msgraph_host": "graph.microsoft.com",
            "rbac_url": "https://pas.windows.net",
        }

    def _callback_extras(self, app, account, q):
        return {"session_state": secrets.token_hex(16)}

    def _denied_params(self):
        return {"error": "access_denied", "error_description": "AADSTS65004: User declined to consent to access the app."}

    def id_claims(self, aud, account, nonce, scope, app):
        now = _now()
        claims = {
            "ver": "2.0", "iss": f"https://login.microsoftonline.com/{account.tenant}/v2.0", "sub": account.sub,
            "aud": aud, "exp": now + 3600, "iat": now, "nbf": now, "tid": account.tenant,
            "oid": hashlib.md5(account.sub.encode()).hexdigest(),
        }
        if "profile" in scope:
            claims |= {"name": account.name, "preferred_username": account.email}
        if "email" in scope and account.email and app is not None and app.optional_email_claim:
            claims["email"] = account.email
        if nonce:
            claims["nonce"] = nonce
        return claims

    def _token(self, req: Request) -> httpx.Response:
        grant, err = self.redeem(req)
        if err:
            return err
        return json_response(200, self._issue(grant) | {"scope": "openid profile email", "ext_expires_in": 3599})

    def token_error(self, error, description, status=400):
        return json_response(status, {"error": error, "error_description": f"AADSTS70008: {description}",
                                      "error_codes": [70008], "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ"),
                                      "trace_id": secrets.token_hex(8), "correlation_id": secrets.token_hex(8)})


class AppleTwin(IdentityTwin):
    name, label, colour = "apple", "Apple", "#000"
    hosts = ("appleid.apple.com",)
    authorize_host, authorize_path = "appleid.apple.com", "/auth/authorize"
    issuer = "https://appleid.apple.com"
    always_id_token = True
    code_ttl = 300

    def __init__(self):
        super().__init__()
        h = "appleid.apple.com"
        self.route("GET", h, "/.well-known/openid-configuration", lambda r: json_response(200, self.discovery()))
        self.route("GET", h, "/auth/keys", lambda r: json_response(200, self.jwks()))
        self.route("POST", h, "/auth/token", self._token)

    def discovery(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": "https://appleid.apple.com/auth/authorize",
            "token_endpoint": "https://appleid.apple.com/auth/token",
            "revocation_endpoint": "https://appleid.apple.com/auth/revoke",
            "jwks_uri": "https://appleid.apple.com/auth/keys",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query", "fragment", "form_post"],
            "subject_types_supported": ["pairwise"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "name"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
            "claims_supported": ["aud", "email", "email_verified", "exp", "iat", "is_private_email", "iss",
                                 "nonce", "nonce_supported", "real_user_status", "sub", "transfer_sub"],
        }

    def _extra_checks(self, q):
        if {"name", "email"} & set(q.get("scope", "").split()) and q.get("response_mode") != "form_post":
            return "invalid_request: response_mode must be form_post when name or email scope is requested."
        return None

    def _denied_params(self):
        return {"error": "user_cancelled_authorize"}

    def _callback_extras(self, app, account, q):
        # The user's name (and e-mail) arrive once: on the first authorisation for this app.
        if (app.client_id, account.sub) in self.authorised or "name" not in q.get("scope", "").split():
            return {}
        user = {"name": {"firstName": account.first_name, "lastName": account.last_name}, "email": self._email(account)}
        return {"user": json.dumps(user)}

    def _email(self, account: Account) -> str | None:
        if account.hide_email:
            return f"{hashlib.sha1(account.sub.encode()).hexdigest()[:10]}@privaterelay.appleid.com"
        return account.email

    def id_claims(self, aud, account, nonce, scope, app):
        now = _now()
        claims = {"iss": self.issuer, "aud": aud, "exp": now + 600, "iat": now, "sub": account.sub,
                  "auth_time": now, "nonce_supported": True, "real_user_status": 2}
        email = self._email(account)
        if email:
            claims |= {"email": email, "email_verified": True, "is_private_email": account.hide_email}
        if nonce:
            claims["nonce"] = nonce
        return claims

    def _client_ok(self, app, req):
        """Apple's client secret is an ES256 JWT signed with the app's key (.p8)."""
        secret = req.form.get("client_secret", "")
        if not app.public_key:
            return False
        try:
            token = jwt.decode(secret, ECKey.import_key(app.public_key), algorithms=["ES256"])
        except (JoseError, ValueError):
            return False
        c, h = token.claims, token.header
        now = time.time()
        return (h.get("kid") == app.key_id and c.get("iss") == app.team_id and c.get("sub") == app.client_id
                and c.get("aud") == self.issuer and c.get("exp", 0) > now and c.get("exp", 0) - c.get("iat", 0) <= 15777000)

    def _token(self, req: Request) -> httpx.Response:
        grant, err = self.redeem(req)
        if err:
            return err
        return json_response(200, self._issue(grant) | {"refresh_token": secrets.token_urlsafe(32)})

    def token_error(self, error, description, status=400):
        return json_response(400, {"error": error})  # Apple sends no description


class FacebookTwin(IdentityTwin):
    """Facebook Login (plain OAuth 2.0) and the Graph API ``/me``."""

    name, label, colour = "facebook", "Facebook", "#1877f2"
    hosts = ("www.facebook.com", "graph.facebook.com")
    authorize_host, authorize_path = "www.facebook.com", "/{version}/dialog/oauth"

    def __init__(self):
        super().__init__()
        self.route("GET", "graph.facebook.com", "/{version}/oauth/access_token", self._token)
        self.route("POST", "graph.facebook.com", "/{version}/oauth/access_token", self._token)
        self.route("GET", "graph.facebook.com", "/{version}/me", self._me)
        self.route("GET", "graph.facebook.com", "/me", self._me)

    def _check_request(self, q):
        q = dict(q, response_type=q.get("response_type", "code"))  # Facebook defaults to code
        return super()._check_request(q)

    def _denied_params(self):
        return {"error": "access_denied", "error_code": "200", "error_description": "Permissions error",
                "error_reason": "user_denied"}

    def id_claims(self, aud, account, nonce, scope, app):
        return {}

    def redeem(self, req):
        return super().redeem(_with_default(req, "grant_type", "authorization_code"))

    def _token(self, req: Request) -> httpx.Response:
        grant, err = self.redeem(req)
        if err:
            return err
        access = secrets.token_urlsafe(32)
        self.access_tokens[access] = (grant.account, grant.scope)
        return json_response(200, {"access_token": access, "token_type": "bearer", "expires_in": 5183944})

    def token_error(self, error, description, status=400):
        return self.graph_error(description, 100 if error != "invalid_grant" else 190)

    def graph_error(self, message: str, code: int = 190) -> httpx.Response:
        return json_response(400, {"error": {"message": message, "type": "OAuthException", "code": code,
                                             "fbtrace_id": secrets.token_urlsafe(8)}})

    def _me(self, req: Request) -> httpx.Response:
        token = req.headers.get("authorization", "").split(" ")[-1] if "authorization" in req.headers else req.query.get("access_token")
        if token not in self.access_tokens:
            return self.graph_error("Invalid OAuth access token - Cannot parse access token")
        account, scope = self.access_tokens[token]
        fields = (req.query.get("fields") or "id,name").split(",")
        me = {"id": account.sub}
        if "name" in fields and account.name:
            me["name"] = account.name
        if "email" in fields and "email" in scope and account.share_email and account.email:
            me["email"] = account.email
        return json_response(200, me)


def _with_default(req: Request, key: str, value: str) -> Request:
    if req.value(key) is None:
        req.query[key] = value
    return req

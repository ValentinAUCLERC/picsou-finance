"""Read-only BoursoBank authentication and accounts sidecar.

BoursoBank has no public API. This service speaks its web front end over plain
HTTPS -- no browser is needed, because there is no JavaScript challenge to
execute: the one anti-bot token (`__brs_mit`) is handed out by the server and
echoed back. The endpoint shapes and the login choreography are taken from the
reference implementation (https://github.com/azerpas/bourso-api) rather than
guessed.

Auth flow:
  POST /initiate {customerId, password}
    → no second factor: {processId: null, mfaRequired: false, sessionState}
    → app push:        {processId, mfaRequired: true, mfaType: "APP_PUSH"}
  POST /complete {processId}
    → holds open while the user approves in the BoursoBank app → {sessionState}
  POST /accounts {sessionState}
    → [{externalId, name, type, balanceEur, cashBalance, positions[], snapshotComplete}]

Only the session cookies are returned to Java, which encrypts them before
storage. Credentials are held in memory for the length of one request and are
never logged; neither are cookies or raw financial responses.

SMS and e-mail second factors are deliberately not implemented: only the app
push is proven upstream, and a half-working OTP path that silently burns login
attempts is worse than a clear MFA_TYPE_UNSUPPORTED.
"""

import asyncio
import html as html_module
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from accounts_parser import (
    AccountKind,
    AccountsFormatError,
    describe_payload,
    guard_symbol_collisions,
    parse_amount,
    parse_dashboard,
    parse_trading_summary,
)
from virtual_pad import VirtualPadError, encode_password, extract_challenge, parse_virtual_pad

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bourso-auth")

BASE_URL = "https://clients.boursobank.com"
LOGIN_PATH = "/connexion/"
VIRTUAL_PAD_PATH = "/connexion/clavier-virtuel?_hinclude=1"
PASSWORD_PATH = "/connexion/saisie-mot-de-passe"
SECURISATION_PATH = "/securisation"
# BoursoBank periodically parks a successful login on an anti-fraud education
# page ("/infos-profil/pedagogie-fraude/...") until the account holder ticks the
# acknowledgment on the real website. The login POST already returned 302, so
# the credentials are valid -- this must never read as INVALID_CREDENTIALS.
FRAUD_EDUCATION_PATH = "/infos-profil/pedagogie-fraude"
VALIDATION_PATH = "/securisation/validation"
ACCOUNTS_PATH = "/dashboard/liste-comptes?rumroute=dashboard.new_accounts&_hinclude=1"
IDENTITY_LIST_PATH = "/connexion/lister-identites"

PENDING_TTL_SECONDS = 600
PENDING_SWEEP_SECONDS = 30
# A push has to be approved by a human on their phone. Stay under the Java
# adapter's 150 s validation timeout so the sidecar is always the one to give up.
APP_VALIDATION_TIMEOUT_SECONDS = 120
APP_VALIDATION_POLL_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 30.0
TRADING_SUMMARY_ATTEMPTS = 3
# Every pending second factor pins an httpx client and its cookie jar. Cheap
# next to a browser, but the backend's per-IP throttle does not bound this
# service in aggregate.
MAX_PENDING = 16

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_pending: dict[str, dict[str, Any]] = {}
_pending_lock = asyncio.Lock()

_BRS_MIT_RE = re.compile(r"__brs_mit=(?P<value>[^;\"'\s]+)")
_FORM_TOKEN_RE = re.compile(
    r'name="form\[_token\]"[^>]*?value="(?P<token>[^"]*)"'
    r'|value="(?P<token_first>[^"]*)"[^>]*?name="form\[_token\]"'
)
_API_URL_RE = re.compile(r'"API_URL"\s*:\s*"(?P<url>[^"]+)"')
_USER_HASH_RE = re.compile(r'"USER_HASH"\s*:\s*"(?P<hash>[^"]+)"')
_DEFAULT_API_BEARER_RE = re.compile(r'"DEFAULT_API_BEARER"\s*:\s*"(?P<token>[^"]+)"')
_STRONG_AUTH_RE = re.compile(r'data-strong-authentication-payload="(?P<payload>[^"]*)"')
_LOGGED_IN_MARKER = 'href="/se-deconnecter"'
_BAD_CREDENTIALS_MARKERS = (
    "Identifiant ou mot de passe invalide",
    "Erreur d&#039;authentification",
    "Erreur d'authentification",
)


_IDENTITY_LINK_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", flags=re.IGNORECASE | re.DOTALL)
_IDENTITY_HREF_RE = re.compile(r"\bhref=[\x27\x22](?P<path>/connexion/changer-identite/[^\x27\x22]+)")
_IDENTITY_LABEL_RE = re.compile(
    r"<span\b[^>]*\bc-menu-list__label\b[^>]*>(?P<label>.*?)</span>",
    flags=re.IGNORECASE | re.DOTALL,
)
_PERSONAL_IDENTITY_EXCLUDED_PREFIXES = ("EI ", "EIRL ", "SAS ", "SASU ", "SARL ", "SCI ")


# ─── Lifecycle ──────────────────────────────────────────────────────────────


async def _pending_sweeper() -> None:
    while True:
        await asyncio.sleep(PENDING_SWEEP_SECONDS)
        try:
            await _cleanup_expired()
        except Exception:
            # One bad sweep must not end the task: without it, expired pending
            # states keep their client and cookie jar until shutdown and
            # MAX_PENDING eventually rejects every new login.
            log.warning("BoursoBank pending sweep failed", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    sweeper = asyncio.create_task(_pending_sweeper())
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass
        await _close_all_pending()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def log_request_duration(request: Request, call_next):
    started_at = time.monotonic()
    try:
        return await call_next(request)
    finally:
        if request.url.path != "/health":
            # Uvicorn percent-decodes the path, so a caller can plant CR/LF in it
            # and forge log lines. Strip controls and bound the length.
            log.info(
                "BoursoBank request completed (path=%s; duration=%.2fs)",
                _log_safe(request.url.path),
                time.monotonic() - started_at,
            )


def _log_safe(value: str) -> str:
    return "".join(char for char in value if char.isprintable())[:200]


# ─── Contract ───────────────────────────────────────────────────────────────


class InitiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customerId: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=100)


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processId: str = Field(min_length=1, max_length=100)
    # Present for contract symmetry with the other connectors and always null:
    # an app push has nothing to type. A code that is sent anyway is refused
    # rather than ignored, so a future SMS path cannot ship half-wired.
    code: None = None


class AccountsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionState: str = Field(min_length=2, max_length=2_000_000)


class TradePayload(BaseModel):
    """One executed securities line, never an aggregate portfolio position."""
    model_config = ConfigDict(extra="forbid")

    externalAccountId: str = Field(min_length=1, max_length=100)
    externalId: str = Field(min_length=1, max_length=128)
    date: str
    side: Literal["BUY", "SELL"]
    label: str = Field(min_length=1, max_length=200)
    isin: str | None = Field(default=None, max_length=12)
    quantity: Decimal
    priceEur: Decimal
    feesEur: Decimal = Decimal("0")


class PositionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isin: str | None = Field(default=None, max_length=12)
    symbol: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    quantity: Decimal
    buyingPriceEur: Decimal | None = None
    currentPrice: Decimal | None = None
    quoteCurrency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    currentValueEur: Decimal
    pnlEur: Decimal | None = None


class AccountPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    externalId: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    type: AccountKind
    balanceEur: Decimal
    cashBalance: Decimal | None = None
    positions: list[PositionPayload]
    # A type-level assertion that a partial read can never be serialised.
    snapshotComplete: Literal[True]


class InitiateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processId: str | None
    mfaRequired: bool
    mfaType: str | None
    sessionState: str | None


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionState: str


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = {str(error["loc"][-1]) for error in exc.errors() if error.get("loc")}
    detail = "MFA_TYPE_UNSUPPORTED" if "code" in fields else "INVALID_DATA"
    return JSONResponse(status_code=400, content={"detail": detail})


# ─── Pending second factors ─────────────────────────────────────────────────


async def _dispose_pending_state(state: dict[str, Any]) -> None:
    client: httpx.AsyncClient | None = state.get("client")
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:
        log.warning("BoursoBank client cleanup failed", exc_info=True)


async def _take_pending(process_id: str) -> dict[str, Any] | None:
    async with _pending_lock:
        return _pending.pop(process_id, None)


async def _cleanup_expired() -> None:
    cutoff = time.time() - PENDING_TTL_SECONDS
    async with _pending_lock:
        expired = [pid for pid, state in _pending.items() if state["created_at"] < cutoff]
        states = [_pending.pop(pid) for pid in expired]
    for state in states:
        await _dispose_pending_state(state)


async def _close_all_pending() -> None:
    async with _pending_lock:
        states = list(_pending.values())
        _pending.clear()
    for state in states:
        await _dispose_pending_state(state)


async def _store_pending(process_id: str, state: dict[str, Any]) -> None:
    async with _pending_lock:
        if len(_pending) >= MAX_PENDING:
            raise HTTPException(status_code=503, detail="UPSTREAM_UNAVAILABLE")
        _pending[process_id] = state


# ─── HTTP plumbing ──────────────────────────────────────────────────────────


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
        # The login POST is only recognised as successful by its 302, and the
        # export/validation steps rely on seeing redirects rather than chasing them.
        follow_redirects=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-FR,fr;q=0.9",
        },
    )


def serialize_cookies(client: httpx.AsyncClient) -> str:
    """Persist the cookie jar with each cookie's own domain and path.

    The trading board lives on `api.boursobank.com` while the login is on
    `clients.boursobank.com`; the session cookies are set on the shared parent
    domain. Re-setting them all on one host would silently stop authenticating
    the trading calls.
    """
    cookies = [
        {
            "name": cookie.name,
            "value": cookie.value or "",
            "domain": cookie.domain or "",
            "path": cookie.path or "/",
        }
        for cookie in client.cookies.jar
    ]
    return json.dumps({"cookies": cookies}, separators=(",", ":"))


def restore_cookies(client: httpx.AsyncClient, raw: str) -> None:
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="INVALID_DATA") from exc
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=400, detail="INVALID_DATA")
    cookies = decoded.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        raise HTTPException(status_code=400, detail="INVALID_DATA")
    for cookie in cookies:
        if not isinstance(cookie, dict) or not isinstance(cookie.get("name"), str):
            raise HTTPException(status_code=400, detail="INVALID_DATA")
        client.cookies.set(
            cookie["name"],
            str(cookie.get("value", "")),
            domain=str(cookie.get("domain") or ""),
            path=str(cookie.get("path") or "/"),
        )


def _first_group(match: re.Match | None, *names: str) -> str | None:
    if match is None:
        return None
    for name in names:
        value = match.group(name)
        if value:
            return value.strip()
    return None


def extract_form_token(page: str) -> str:
    token = _first_group(_FORM_TOKEN_RE.search(page), "token", "token_first")
    if not token:
        raise AccountsFormatError("UPSTREAM_FORMAT_CHANGED", "Login page carried no form token")
    return token


def extract_brs_config(page: str) -> tuple[str, str]:
    """Read `API_URL` and `USER_HASH` out of the inline `window.BRS_CONFIG`.

    Only the two fields that matter are pulled out; parsing the whole object
    would make an unrelated new field able to break the connector. The JSON
    escapes its slashes, hence the unescape.
    """
    api_url = _first_group(_API_URL_RE.search(page), "url")
    user_hash = _first_group(_USER_HASH_RE.search(page), "hash")
    if not api_url or not user_hash:
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Home page carried no API_URL/USER_HASH"
        )
    return api_url.replace("\\/", "/").rstrip("/"), user_hash


def extract_default_api_bearer(page: str) -> str | None:
    """Return BoursoBank's short-lived browser API token, if one is present."""
    return _first_group(_DEFAULT_API_BEARER_RE.search(page), "token")


async def _bootstrap(client: httpx.AsyncClient) -> str:
    """Clear the `__brs_mit` cookie gate and return the real login page.

    BoursoBank first serves a page that sets `__brs_mit` from JavaScript and
    reloads. There is nothing to execute -- the value is right there in the body
    -- so it is read, set as a cookie, and the page re-requested.
    """
    response = await client.get(LOGIN_PATH)
    body = response.text
    if _LOGGED_IN_MARKER in body:
        return body

    token = _first_group(_BRS_MIT_RE.search(body), "value")
    if token is None and "__brs_mit" in client.cookies:
        token = client.cookies["__brs_mit"]
    if token is None:
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Login page did not hand out a __brs_mit token"
        )

    client.cookies.set("__brs_mit", token, domain=".boursobank.com", path="/")
    client.cookies.set("brsDomainMigration", "migrated", domain=".boursobank.com", path="/")
    return (await client.get(LOGIN_PATH)).text


async def _login(client: httpx.AsyncClient, customer_id: str, password: str) -> None:
    login_page = await _bootstrap(client)
    form_token = extract_form_token(login_page)

    pad_page = (await client.get(VIRTUAL_PAD_PATH)).text
    keys = parse_virtual_pad(pad_page)
    challenge = extract_challenge(pad_page)

    fields = {
        "form[clientNumber]": customer_id,
        "form[password]": encode_password(password, keys),
        "form[matrixRandomChallenge]": challenge,
        "form[_token]": form_token,
        "form[fakePassword]": "•" * len(password),
        "form[passwordAck]": '{"ry":[],"pt":[],"js":true}',
        "form[platformAuthenticatorAvailable]": "1",
        "form[ajx]": "1",
    }
    # BoursoBank's login form is submitted as multipart, not urlencoded.
    response = await client.post(
        PASSWORD_PATH,
        files={name: (None, value) for name, value in fields.items()},
    )
    if response.status_code != 302:
        body = response.text
        if any(marker in body for marker in _BAD_CREDENTIALS_MARKERS):
            raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
        log.warning("BoursoBank login returned HTTP %s instead of a redirect", response.status_code)
        raise HTTPException(status_code=502, detail="UPSTREAM_FORMAT_CHANGED")


def _personal_identity_path(page: str) -> str:
    """Return the sole non-business profile switch link from the selector page."""
    candidates: list[tuple[str, str]] = []
    for link in _IDENTITY_LINK_RE.finditer(page):
        if "data-switch-account" not in link.group("attrs"):
            continue
        href = _IDENTITY_HREF_RE.search(link.group("attrs"))
        label = _IDENTITY_LABEL_RE.search(link.group("body"))
        if not href or not label:
            continue
        plain_label = html_module.unescape(re.sub(r"<[^>]+>", "", label.group("label"))).strip()
        candidates.append((href.group("path"), plain_label))

    personal_paths = [
        path
        for path, label in candidates
        if not label.upper().startswith(_PERSONAL_IDENTITY_EXCLUDED_PREFIXES)
    ]
    if len(personal_paths) != 1:
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED",
            "Identity selector did not expose a unique personal profile",
        )
    return personal_paths[0]


async def _home(client: httpx.AsyncClient) -> str:
    response = await client.get("/", follow_redirects=True)
    if response.url.path != IDENTITY_LIST_PATH:
        return response.text

    response = await client.get(_personal_identity_path(response.text), follow_redirects=True)
    if response.url.path == IDENTITY_LIST_PATH:
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Personal identity selection returned to the selector"
        )
    log.info("BoursoBank selected the configured personal identity")
    return response.text


def is_fraud_education_page(home: str) -> bool:
    """True when BoursoBank parked the login on its fraud-education notice.

    Pure so the initiate() branch ordering stays unit-testable without driving
    the login flow: a fraud landing is neither the dashboard marker nor the
    securisation path, and must not fall through to INVALID_CREDENTIALS.
    """
    return FRAUD_EDUCATION_PATH in home


def _strong_auth_params(page: str) -> tuple[str, str]:
    """Pull `resourceId` and `formState` out of the securisation payload."""
    raw = _first_group(_STRONG_AUTH_RE.search(page), "payload")
    if not raw:
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Securisation page carried no authentication payload"
        )
    try:
        payload = json.loads(html_module.unescape(raw))
        params = payload["challenges"][0]["parameters"]["formScreen"]["actions"]["check"]["api"][
            "params"
        ]
        resource_id = params["resourceId"]
        form_state = params["formState"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Authentication challenge payload has an unknown shape"
        ) from exc
    if not isinstance(resource_id, str) or not isinstance(form_state, str):
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Authentication challenge is missing resourceId/formState"
        )
    return resource_id, form_state


async def _start_app_push(client: httpx.AsyncClient) -> dict[str, Any]:
    """Ask BoursoBank to push a validation to the customer's phone."""
    await client.get(SECURISATION_PATH, follow_redirects=True)
    page = (await client.get(VALIDATION_PATH, follow_redirects=True)).text

    if "brs-otp-webtoapp" not in page:
        other = re.search(r"brs-otp-(?P<kind>sms|email)", page)
        if other:
            log.info("BoursoBank asked for an unsupported second factor: %s", other.group("kind"))
            raise HTTPException(status_code=501, detail="MFA_TYPE_UNSUPPORTED")
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Securisation page offered no known second factor"
        )

    api_url, user_hash = extract_brs_config(page)
    resource_id, form_state = _strong_auth_params(page)
    validation_token = extract_form_token(page)

    response = await client.post(
        f"{api_url}/fr-FR/_user_/_{user_hash}_/session/challenge/startwebtoapp/{resource_id}",
        content=json.dumps({"formState": form_state}),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    if response.status_code != 200 or not _json_success(response):
        log.warning("BoursoBank refused to send the app push (HTTP %s)", response.status_code)
        raise HTTPException(status_code=502, detail="UPSTREAM_UNAVAILABLE")

    return {
        "apiUrl": api_url,
        "userHash": user_hash,
        "resourceId": resource_id,
        "formState": form_state,
        "validationToken": validation_token,
    }


def _json_success(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("success") is True


async def _await_app_push(client: httpx.AsyncClient, state: dict[str, Any]) -> bool:
    """Poll the challenge endpoint the way BoursoBank's own page does."""
    deadline = time.monotonic() + APP_VALIDATION_TIMEOUT_SECONDS
    check_url = (
        f"{state['apiUrl']}/_user_/_{state['userHash']}_"
        f"/session/challenge/checkwebtoapp/{state['resourceId']}"
    )
    while time.monotonic() < deadline:
        response = await client.post(
            check_url,
            content=json.dumps({"formState": state["formState"]}),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        if response.status_code == 200 and _json_success(response):
            return True
        await asyncio.sleep(APP_VALIDATION_POLL_SECONDS)
    return False


async def _confirm_validation(client: httpx.AsyncClient, validation_token: str) -> None:
    """Submit the securisation form once the push has been approved."""
    await client.post(
        VALIDATION_PATH,
        data={"form[_token]": validation_token},
        headers={
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}{VALIDATION_PATH}",
        },
    )


# ─── Accounts ───────────────────────────────────────────────────────────────


async def _fetch_trading_account(
    client: httpx.AsyncClient, api_url: str, user_hash: str, account_id: str,
    account_path: str | None = None, api_bearer: str | None = None,
) -> dict[str, Any]:
    response: httpx.Response | None = None
    account_api_url, account_user_hash = api_url, user_hash
    account_api_bearer = api_bearer
    for attempt in range(TRADING_SUMMARY_ATTEMPTS):
        headers = {"Accept": "application/json"}
        if account_api_bearer:
            headers["Authorization"] = f"Bearer {account_api_bearer}"
        if account_path:
            headers["Referer"] = f"{BASE_URL}{account_path}"
        response = await client.get(
            f"{account_api_url}/_user_/_{account_user_hash}_/trading/accounts/summary/{account_id}",
            params={
                "_host": "tradingboard.boursobank.com",
                "position": "ACCOUNTING",
                "responseFormat": "true",
            },
            headers=headers,
        )
        # On some profile configurations the dashboard's global BRS_CONFIG is
        # accepted by the web pages but rejected (404) by the trading service.
        # The account page carries the account-scoped configuration, so use it
        # before declaring the trading gateway unavailable.
        if response.status_code == 404 and attempt == 0 and account_path:
            log.info(
                "BoursoBank trading summary 404 body (account=%s…): %s",
                account_id[:8], response.text[:2_000],
            )
            account_page = await client.get(
                account_path,
                headers={"X-Requested-With": "XMLHttpRequest"},
                follow_redirects=True,
            )
            if account_page.status_code == 200:
                # Retained only for the requested connector audit: this lets
                # us inspect the dynamically loaded positions endpoint without
                # putting a raw account page in application logs.
                with open(f"/tmp/bourso-positions-{account_id}.html", "w", encoding="utf-8") as audit_capture:
                    audit_capture.write(account_page.text)
                log.info(
                    "BoursoBank positions page structure (account=%s…; tables=%s; positionLinks=%s)",
                    account_id[:8],
                    _movement_table_signature(account_page.text)[:5],
                    re.findall(r'(?:src|href)=["\']([^"\']*positions[^"\']*)', account_page.text)[:10],
                )
                try:
                    account_api_url, account_user_hash = extract_brs_config(account_page.text)
                    account_api_bearer = extract_default_api_bearer(account_page.text)
                    log.info("BoursoBank: retrying summary with account-scoped API configuration")
                except AccountsFormatError:
                    pass
        # Bourso's trading gateway has been observed returning 404 while its
        # backend is degraded, then 503 on the next request. It is not a page
        # format change: retry it with the other transient gateway failures.
        if response.status_code not in {404, 429, 500, 502, 503, 504} or attempt == TRADING_SUMMARY_ATTEMPTS - 1:
            break
        delay = 0.4 * (attempt + 1)
        log.info(
            "BoursoBank trading summary is temporarily unavailable for account %s…; retrying in %.1fs",
            account_id[:8], delay,
        )
        await asyncio.sleep(delay)
    assert response is not None
    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
    if response.status_code != 200:
        log.warning(
            "BoursoBank trading summary failed for account %s… (HTTP %s)",
            account_id[:8],
            response.status_code,
        )
        if response.status_code in {404, 429} or response.status_code >= 500:
            raise HTTPException(status_code=502, detail="UPSTREAM_UNAVAILABLE")
        raise HTTPException(status_code=502, detail="UPSTREAM_FORMAT_CHANGED")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AccountsFormatError(
            "UPSTREAM_FORMAT_CHANGED", "Trading summary was not JSON"
        ) from exc
    try:
        return parse_trading_summary(payload, account_id)
    except AccountsFormatError:
        log.warning(
            "BoursoBank trading summary has an unsupported shape for account %s…: %s",
            account_id[:8],
            describe_payload(payload),
        )
        raise


async def _collect_accounts(client: httpx.AsyncClient) -> list[AccountPayload]:
    home = await _home(client)
    if _LOGGED_IN_MARKER not in home:
        raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
    api_url, user_hash = extract_brs_config(home)
    api_bearer = extract_default_api_bearer(home)

    dashboard = (await client.get(ACCOUNTS_PATH, headers={"X-Requested-With": "XMLHttpRequest"})).text
    accounts, third_party = parse_dashboard(dashboard)
    if third_party:
        log.info(
            "BoursoBank: skipped %d aggregated third-party account(s) -- out of scope", third_party
        )

    payloads: list[dict[str, Any]] = []
    for account in accounts:
        entry: dict[str, Any] = {
            "externalId": f"bourso_{account['id']}",
            "name": account["name"],
            "type": account["type"],
            "balanceEur": account["balanceEur"],
            "cashBalance": None,
            "positions": [],
            "snapshotComplete": True,
        }
        if account["section"] == "trading":
            log.info(
                "BoursoBank: loading trading account %s (route=%s; id=%s…)",
                account["name"], account["route"], account["id"][:8],
            )
            summary = await _fetch_trading_account(
                client, api_url, user_hash, account["id"],
                f"/compte/{account['route']}/{account['id']}",
                api_bearer,
            )
            # The trading board is authoritative over the dashboard tile: it is
            # the figure the two reconciliations above were run against.
            entry["balanceEur"] = summary["totalEur"]
            entry["cashBalance"] = summary["cashEur"]
            entry["positions"] = summary["positions"]
            guard_symbol_collisions(entry["positions"])
        payloads.append(entry)

    unresolved = sum(
        1 for entry in payloads for position in entry["positions"] if not position["isin"]
    )
    if unresolved:
        log.info("BoursoBank: %d instrument(s) kept their symbol as ticker", unresolved)
    return [AccountPayload.model_validate(entry) for entry in payloads]


_TABLE_RE = re.compile(r"<table\b[^>]*>(?P<table>.*?)</table>", re.IGNORECASE | re.DOTALL)
_ROW_RE = re.compile(r"<tr\b(?P<attrs>[^>]*)>(?P<row>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(?P<cell>.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_DETAIL_ID_RE = re.compile(r"data-modal-alert-behavior=[\"'][^\"']*?(?P<id>\d+)", re.IGNORECASE)
_ACCOUNT_OPENING_DATE_RE = re.compile(
    r"Date d(?:['’]|&#039;)ouverture(?: fiscale)? du compte.*?(?P<date>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE | re.DOTALL,
)
_ISIN_IN_TEXT_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")


def _plain(markup: str) -> str:
    return " ".join(html_module.unescape(_TAG_RE.sub(" ", markup)).split())


def _date_value(raw: str) -> str | None:
    raw = _plain(raw)
    for pattern in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            pass
    french_months = {
        "janvier": 1, "janv": 1, "février": 2, "fevrier": 2, "févr": 2, "fevr": 2,
        "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "juil": 7,
        "août": 8, "aout": 8, "septembre": 9, "sept": 9, "octobre": 10, "oct": 10,
        "novembre": 11, "nov": 11, "décembre": 12, "decembre": 12, "déc": 12, "dec": 12,
    }
    match = re.fullmatch(r"(?P<day>\d{1,2})\s+(?P<month>[a-zéû]+)\.?\s+(?P<year>\d{4})", raw.lower())
    if match and (month := french_months.get(match.group("month"))):
        return datetime(int(match.group("year")), month, int(match.group("day"))).date().isoformat()
    return None


def _trading_periods(page: str) -> list[str]:
    """Return every monthly filter value from the account's opening month."""
    opening_match = _ACCOUNT_OPENING_DATE_RE.search(page)
    opening = _date_value(opening_match.group("date")) if opening_match else None
    if opening is None:
        # Keep the connector bounded when BoursoBank removes the opening-date
        # card. The visible form still supplies the latest selectable months.
        return re.findall(r'<option\s+value=["\'](?P<period>\d{1,2}-\d{4})', page)

    first = datetime.strptime(opening, "%Y-%m-%d").date().replace(day=1)
    current = datetime.now().date().replace(day=1)
    periods: list[str] = []
    while current >= first:
        periods.append(f"{current.month}-{current.year}")
        current = current.replace(year=current.year - 1, month=12) if current.month == 1 else current.replace(month=current.month - 1)
    return periods


def _history_rows(page: str) -> list[tuple[str, str, str]]:
    """Return (date, operation label, detail id) rows from Bourso's movements table."""
    out: list[tuple[str, str, str]] = []
    for table_match in _TABLE_RE.finditer(page):
        rows = list(_ROW_RE.finditer(table_match.group("table")))
        if not rows:
            continue
        header = [_plain(cell.group("cell")).lower() for cell in _CELL_RE.finditer(rows[0].group("row"))]
        date_index = next((i for i, value in enumerate(header) if "date" in value), None)
        label_index = next((i for i, value in enumerate(header) if "nature" in value or "opération" in value or "operation" in value), None)
        if date_index is None or label_index is None:
            continue
        for row in rows[1:]:
            detail = _DETAIL_ID_RE.search(row.group(0))
            if not detail:
                continue
            cells = [_plain(cell.group("cell")) for cell in _CELL_RE.finditer(row.group("row"))]
            if max(date_index, label_index) >= len(cells):
                continue
            date = _date_value(cells[date_index])
            if date:
                out.append((date, cells[label_index], detail.group("id")))
    return out


def _movement_table_signature(page: str) -> list[dict[str, Any]]:
    """Describe the rendered movement tables without retaining their HTML."""
    signatures: list[dict[str, Any]] = []
    for table_match in _TABLE_RE.finditer(page):
        rows = list(_ROW_RE.finditer(table_match.group("table")))
        if not rows:
            continue
        cells_by_row = [
            [_plain(cell.group("cell"))[:120] for cell in _CELL_RE.finditer(row.group("row"))]
            for row in rows
        ]
        signatures.append({
            "headers": cells_by_row[0],
            "sampleRows": [cells for cells in cells_by_row[1:3] if cells],
            "detailLinks": len(_DETAIL_ID_RE.findall(table_match.group("table"))),
        })
    return signatures


class _MovementTableParser(HTMLParser):
    """Extract direct table cells while preserving the real DOM nesting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, str]]]] = []
        self._table_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_stack.append({"rows": [], "row": None, "cell": None})
        elif tag == "tr" and self._table_stack:
            self._table_stack[-1]["row"] = []
        elif tag in {"td", "th"} and self._table_stack and self._table_stack[-1]["row"] is not None:
            self._table_stack[-1]["cell"] = {"tag": tag, "parts": []}

    def handle_data(self, data: str) -> None:
        if self._table_stack and self._table_stack[-1]["cell"] is not None:
            self._table_stack[-1]["cell"]["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._table_stack:
            return
        current = self._table_stack[-1]
        if tag in {"td", "th"} and current["cell"] is not None:
            cell = current["cell"]
            current["row"].append((cell["tag"], " ".join(cell["parts"]).strip()))
            current["cell"] = None
        elif tag == "tr" and current["row"] is not None:
            current["rows"].append(current["row"])
            current["row"] = None
        elif tag == "table":
            self.tables.append(self._table_stack.pop()["rows"])


def _direct_table_trades(page: str, external_account_id: str, period: str) -> list[TradePayload]:
    """Parse the current BoursoBank monthly securities table (no detail URL)."""
    parser = _MovementTableParser()
    parser.feed(page)
    trades: list[TradePayload] = []
    for table_number, rows in enumerate(parser.tables):
        if len(rows) < 2:
            continue
        headers = [_plain(value).lower() for _, value in rows[0]]
        # Bourso's responsive markup repeats a hidden value-date cell inside
        # some rows, so data cells are not always aligned with the 8 headers.
        # The ISIN column is the reliable anchor: label comes immediately
        # before it and amount/quantity/price immediately after it.
        if not any("isin" in value for value in headers):
            continue
        for row_number, row in enumerate(rows[1:]):
            cells = [_plain(value) for _, value in row]
            isin_index = next(
                (i for i, value in enumerate(cells) if _ISIN_IN_TEXT_RE.fullmatch(value.upper())),
                None,
            )
            if isin_index is None or isin_index < 1 or isin_index + 2 >= len(cells):
                continue
            side = next((parsed for value in cells[:isin_index] if (parsed := _trade_side(value))), None)
            date = next((parsed for value in cells[:isin_index] if (parsed := _date_value(value))), None)
            quantity = parse_amount(cells[isin_index + 2])
            if side is None or date is None or quantity is None or quantity <= 0:
                continue
            amount = parse_amount(cells[isin_index + 1])
            price = parse_amount(cells[isin_index + 3]) if isin_index + 3 < len(cells) else None
            if price is None or price <= 0:
                price = abs(amount / quantity) if amount is not None and amount != 0 else None
            if price is None or price <= 0:
                continue
            label = cells[isin_index - 1]
            isin_match = _ISIN_IN_TEXT_RE.fullmatch(cells[isin_index].upper())
            trades.append(TradePayload(
                externalAccountId=external_account_id,
                externalId=f"bourso-trade:{external_account_id}:{period}:{table_number}:{row_number}",
                date=date,
                side=side,
                label=label[:200],
                isin=isin_match.group(1) if isin_match else None,
                quantity=quantity,
                priceEur=price,
            ))
    return trades


def _trade_side(operation: str) -> str | None:
    normalized = _plain(operation).lower()
    if any(word in normalized for word in ("vente", "rachat", "désinvest", "desinvest")):
        return "SELL"
    if any(word in normalized for word in ("achat", "souscription", "prélèvement", "prelevement", "versement")):
        return "BUY"
    return None


def _detail_trades(page: str, external_account_id: str, operation_id: str, date: str, operation: str) -> list[TradePayload]:
    side = _trade_side(operation)
    if side is None:
        return []
    trades: list[TradePayload] = []
    # The detail table is the only Bourso page which has one row per security:
    # label, value date, unit price, quantity, total. Ignore all other tables.
    for table_match in _TABLE_RE.finditer(page):
        rows = list(_ROW_RE.finditer(table_match.group("table")))
        for row_number, row in enumerate(rows):
            cells = [_plain(cell.group("cell")) for cell in _CELL_RE.finditer(row.group("row"))]
            if len(cells) < 5:
                continue
            price = parse_amount(cells[2])
            quantity = parse_amount(cells[3])
            if price is None or quantity is None or price < 0 or quantity <= 0:
                continue
            label = cells[0]
            isin_match = _ISIN_IN_TEXT_RE.search(label.upper())
            trades.append(TradePayload(
                externalAccountId=external_account_id,
                externalId=f"bourso-trade:{external_account_id}:{operation_id}:{row_number}",
                date=date,
                side=side,
                label=label[:200],
                isin=isin_match.group(1) if isin_match else None,
                quantity=quantity,
                priceEur=price,
            ))
    return trades


async def _collect_trades(client: httpx.AsyncClient) -> list[TradePayload]:
    """Read every detailed PEA/CTO movement without treating a partial history as holdings data."""
    home = await _home(client)
    if _LOGGED_IN_MARKER not in home:
        raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
    dashboard = await client.get(ACCOUNTS_PATH, follow_redirects=True)
    accounts, _ = parse_dashboard(dashboard.text)
    trades: list[TradePayload] = []
    for account in accounts:
        if account["section"] != "trading" or account["route"] not in {"ord", "pea", "pea-pme"}:
            continue
        base_path = f"/compte/{account['route']}/{account['id']}"
        # PEA and PEA-PME share BoursoBank's `ord` URL namespace. The
        # accountType query string is what switches the movements component
        # to the PEA view.
        movement_params = {"showza": "0"}
        # The public URL is an application shell. Bourso's own movements
        # component asks the server for this hinclude fragment, which contains
        # the list rows and their detail-operation links.
        response = await client.get(
            base_path + "/mouvements",
            params=movement_params,
            headers={"X-Requested-With": "XMLHttpRequest"},
            follow_redirects=True,
        )
        if response.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
        if response.status_code != 200:
            log.info("BoursoBank movements unavailable for account %s… (HTTP %s)", account["id"][:8], response.status_code)
            continue
        form_token = extract_form_token(response.text)
        pages: list[tuple[str, str]] = []
        monthly_signatures: list[tuple[str, list[dict[str, Any]]]] = []
        # BoursoBank's trading history is a form with one selectable calendar
        # month, not the usual account-movement paginator. Query every month
        # individually so lots are backfilled only from their real activity.
        for period in _trading_periods(response.text):
            monthly = await client.get(
                base_path + "/mouvements",
                params={
                    **movement_params,
                    "form[period]": period,
                    "form[type]": "TIT",
                    "form[_token]": form_token,
                    "form[submit]": "",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
                follow_redirects=True,
            )
            if monthly.status_code in (401, 403):
                raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
            if monthly.status_code == 200:
                pages.append((period, monthly.text))
                signature = _movement_table_signature(monthly.text)
                if any(item["sampleRows"] for item in signature):
                    monthly_signatures.append((period, signature))
        direct_trades = [
            trade for period, page in pages
            for trade in _direct_table_trades(page, f"bourso_{account['id']}", period)
        ]
        trades.extend(direct_trades)
        history = [row for _, page in pages for row in _history_rows(page)]
        if not history:
            headers = []
            for table_match in _TABLE_RE.finditer(response.text):
                first = _ROW_RE.search(table_match.group("table"))
                if first:
                    headers.append([_plain(cell.group("cell"))[:60] for cell in _CELL_RE.finditer(first.group("row"))])
            log.info(
                "BoursoBank movements page has no recognised detail rows (account=%s…; initialTables=%s; monthlyTables=%s)",
                account["id"][:8], headers[:4], monthly_signatures[:3],
            )
        for date, operation, detail_id in history:
            detail = await client.get(f"{base_path}/mouvement/{detail_id}", follow_redirects=True)
            if detail.status_code in (401, 403):
                raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
            if detail.status_code == 200:
                trades.extend(_detail_trades(detail.text, f"bourso_{account['id']}", detail_id, date, operation))
    log.info("BoursoBank parsed %d detailed trade line(s)", len(trades))
    return trades


# ─── Routes ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/initiate", response_model=InitiateResponse)
async def initiate(req: InitiateRequest) -> dict:
    await _cleanup_expired()
    process_id = str(uuid.uuid4())
    client = _new_client()
    keep_client = False
    try:
        await _login(client, req.customerId, req.password)
        home = await _home(client)

        if _LOGGED_IN_MARKER in home:
            return {
                "processId": None,
                "mfaRequired": False,
                "mfaType": None,
                "sessionState": serialize_cookies(client),
            }

        if SECURISATION_PATH not in home:
            if is_fraud_education_page(home):
                # The password was accepted (login POST 302) but BoursoBank
                # wants the account holder to acknowledge its fraud-prevention
                # notice on the real website first. Never auto-tick it: it is
                # a legal notice only the holder can accept.
                raise HTTPException(status_code=401, detail="FRAUD_ACK_REQUIRED")
            # The login POST redirected somewhere that is neither the dashboard
            # nor a second factor. BoursoBank answers a wrong password this way
            # once the form has already been accepted.
            raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")

        state = await _start_app_push(client)
        state["client"] = client
        state["created_at"] = time.time()
        await _store_pending(process_id, state)
        keep_client = True
        return {
            "processId": process_id,
            "mfaRequired": True,
            "mfaType": "APP_PUSH",
            "sessionState": None,
        }
    except AccountsFormatError as exc:
        log.warning("BoursoBank login rejected (code=%s): %s", exc.code, exc)
        raise HTTPException(status_code=502, detail=exc.code) from exc
    except VirtualPadError as exc:
        log.warning("BoursoBank virtual keyboard could not be decoded: %s", exc)
        raise HTTPException(status_code=502, detail="UPSTREAM_FORMAT_CHANGED") from exc
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        log.warning("BoursoBank authentication initiation failed", exc_info=True)
        raise HTTPException(status_code=502, detail="UPSTREAM_UNAVAILABLE") from exc
    except Exception as exc:
        log.exception("Unexpected BoursoBank authentication initiation failure")
        raise HTTPException(status_code=500, detail="INTERNAL_ERROR") from exc
    finally:
        if not keep_client:
            await client.aclose()


@app.post("/complete", response_model=SessionResponse)
async def complete(req: CompleteRequest) -> dict:
    await _cleanup_expired()
    state = await _take_pending(req.processId)
    if not state:
        raise HTTPException(status_code=410, detail="AUTH_ATTEMPT_EXPIRED")
    if state["created_at"] < time.time() - PENDING_TTL_SECONDS:
        await _dispose_pending_state(state)
        raise HTTPException(status_code=410, detail="AUTH_ATTEMPT_EXPIRED")

    client: httpx.AsyncClient = state["client"]
    try:
        if not await _await_app_push(client, state):
            raise HTTPException(status_code=408, detail="APP_VALIDATION_TIMEOUT")
        await _confirm_validation(client, state["validationToken"])
        if _LOGGED_IN_MARKER not in await _home(client):
            raise HTTPException(status_code=502, detail="UPSTREAM_FORMAT_CHANGED")
        return {"sessionState": serialize_cookies(client)}
    except AccountsFormatError as exc:
        log.warning("BoursoBank validation rejected (code=%s): %s", exc.code, exc)
        raise HTTPException(status_code=502, detail=exc.code) from exc
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        log.warning("BoursoBank authentication completion failed", exc_info=True)
        raise HTTPException(status_code=502, detail="UPSTREAM_UNAVAILABLE") from exc
    except Exception as exc:
        log.exception("Unexpected BoursoBank authentication completion failure")
        raise HTTPException(status_code=500, detail="INTERNAL_ERROR") from exc
    finally:
        await _dispose_pending_state(state)


@app.post("/accounts", response_model=list[AccountPayload])
async def accounts(req: AccountsRequest) -> list[AccountPayload]:
    client = _new_client()
    try:
        # Inside the try, not before it: a malformed session raises, and a
        # client created outside would never reach the `finally` that closes it
        # -- one leaked connection pool per bad request.
        restore_cookies(client, req.sessionState)
        return await _collect_accounts(client)
    except AccountsFormatError as exc:
        log.warning("BoursoBank accounts payload rejected (code=%s): %s", exc.code, exc)
        raise HTTPException(status_code=502, detail=exc.code) from exc
    except ValidationError as exc:
        log.warning("BoursoBank accounts payload failed validation: %s", exc.error_count())
        raise HTTPException(status_code=502, detail="INVALID_DATA") from exc
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        log.warning("BoursoBank accounts fetch failed", exc_info=True)
        raise HTTPException(status_code=502, detail="UPSTREAM_UNAVAILABLE") from exc
    except Exception as exc:
        log.exception("Unexpected BoursoBank accounts failure")
        raise HTTPException(status_code=500, detail="INTERNAL_ERROR") from exc
    finally:
        await client.aclose()


@app.post("/trades", response_model=list[TradePayload])
async def trades(req: AccountsRequest) -> list[TradePayload]:
    client = _new_client()
    try:
        restore_cookies(client, req.sessionState)
        return await _collect_trades(client)
    except AccountsFormatError as exc:
        log.warning("BoursoBank movements payload rejected (code=%s): %s", exc.code, exc)
        raise HTTPException(status_code=502, detail=exc.code) from exc
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        log.warning("BoursoBank movements fetch failed", exc_info=True)
        raise HTTPException(status_code=502, detail="UPSTREAM_UNAVAILABLE") from exc
    except Exception as exc:
        log.exception("Unexpected BoursoBank movements failure")
        raise HTTPException(status_code=500, detail="INTERNAL_ERROR") from exc
    finally:
        await client.aclose()

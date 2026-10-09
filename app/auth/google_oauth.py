"""Google OAuth 2.0 authentication for the Business Profile APIs.

This module ONLY handles OAuth authentication/authorization:

* creating the Google authorization URL,
* the required OAuth scopes,
* the OAuth callback (authorization code -> credentials),
* loading / saving / refreshing the credentials, which are stored encrypted
  in PostgreSQL (``DATABASE_URL``; see :mod:`app.auth.token_store`).

It never fetches reviews and never publishes replies.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.auth import token_store
from app.config import (
    GOOGLE_AUTH_URL,
    GOOGLE_SCOPE_BUSINESS_MANAGE,
    GOOGLE_TOKEN_URL,
    settings,
)

logger = logging.getLogger(__name__)

# The single Business Profile management scope required by this application.
SCOPES = [GOOGLE_SCOPE_BUSINESS_MANAGE]

router = APIRouter(prefix="/auth/google", tags=["Google OAuth"])

# Single-user local tool: remember the in-progress OAuth flow so the callback
# uses the SAME Flow instance that created the authorization URL. This is
# required: the flow generates a PKCE code_verifier at URL-creation time and
# must send it back during the code exchange — a fresh Flow (without the
# verifier) makes Google reject the code with 400.
_current_state: str | None = None
_current_flow: Flow | None = None

# Google OAuth client IDs always look like:
# 123456789012-abc123def456.apps.googleusercontent.com
_CLIENT_ID_RE = re.compile(r"^\d+-[a-z0-9]+\.apps\.googleusercontent\.com$")

# How often the cached credentials are compared with the database row, so a
# new authorization or a logout made by another instance is picked up.
CACHE_RECHECK_SECONDS = 60


@dataclass
class _CacheEntry:
    credentials: Credentials
    version: int  # database row version these credentials were loaded/saved as
    checked_at: float  # time.monotonic() of the last database check
    unsaved: bool = False  # refreshed in memory, database write failed


# Process-wide credentials cache: one Credentials object shared by every
# Google call, so an access token is refreshed once per expiry (not per call)
# and the database is read at most every CACHE_RECHECK_SECONDS. The lock
# serializes loads and refreshes across the automation's worker threads.
_credentials_lock = threading.RLock()
_cache: _CacheEntry | None = None


class GoogleOAuthError(Exception):
    """Google OAuth is missing, unusable, or failed."""


class GoogleOAuthNotConfiguredError(GoogleOAuthError):
    """OAuth client settings are not present in the environment / .env."""


class GoogleTokenStoreError(GoogleOAuthError):
    """The credential database is not configured or unavailable.

    Not an authentication failure: re-authorizing does not help, so the API
    answers 503 instead of 401.
    """


def _require_oauth_config() -> None:
    """Raise a clean error when the OAuth client is not configured."""
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLIENT_ID", settings.google_client_id),
            ("GOOGLE_CLIENT_SECRET", settings.google_client_secret),
            ("GOOGLE_REDIRECT_URI", settings.google_redirect_uri),
        )
        if not value
    ]
    if missing:
        raise GoogleOAuthNotConfiguredError(
            "Google OAuth is not configured. Set "
            f"{', '.join(missing)} in the .env file (see README)."
        )


def _build_flow() -> Flow:
    """Create an OAuth 2.0 flow from the configured client credentials."""
    _require_oauth_config()
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": GOOGLE_AUTH_URL,
            "token_uri": GOOGLE_TOKEN_URL,
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )


def get_authorization_url() -> tuple[str, str]:
    """Create the Google authorization URL the user must visit.

    ``access_type=offline`` + ``prompt=consent`` make sure Google returns a
    refresh token on first consent, so the app can re-authenticate later
    without user interaction.
    """
    flow = _build_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        # Google requires the literal strings "true"/"false" here — a Python
        # bool would be stringified to "True" and rejected with 400.
        include_granted_scopes="true",
    )
    global _current_state, _current_flow
    _current_state = state
    _current_flow = flow
    logger.info("Google authorization URL created")
    return authorization_url, state


def complete_authorization(code: str, state: str) -> Credentials:
    """Exchange the authorization code for credentials and save them.

    Must reuse the Flow instance that created the authorization URL (it
    holds the PKCE code_verifier Google expects in the code exchange).

    Raises GoogleOAuthError when the state does not match or Google rejects
    the code, and GoogleTokenStoreError when the credentials cannot be saved
    to the database. On success the credentials are stored.
    """
    global _current_flow
    if _current_state is not None and state != _current_state:
        logger.warning("OAuth callback state did not match the started flow")
        raise GoogleOAuthError(
            "OAuth state mismatch. Open the authorization URL again and "
            "complete the flow."
        )
    if _current_flow is None:
        # Server restarted mid-flow: the PKCE verifier is gone, so Google
        # will reject the code. Ask the user to start a fresh flow.
        logger.warning(
            "OAuth callback received but no in-progress flow (server "
            "restarted?); building a fresh flow — code exchange will likely "
            "fail until a new authorization URL is used"
        )
        _current_flow = _build_flow()
    try:
        _current_flow.fetch_token(code=code)
    except Exception as exc:  # network error or invalid_code from Google
        logger.error("Failed to exchange OAuth code for credentials: %s", exc)
        hint = (
            " If the server restarted since the URL was requested, get a new "
            "authorization URL and complete the flow again."
        )
        raise GoogleOAuthError(
            f"Google rejected the authorization code ({exc}). Please retry "
            f"the authorization flow.{hint}"
        ) from exc
    credentials = _current_flow.credentials
    if not credentials.refresh_token:
        # Storing it would replace a working refresh token with credentials
        # that stop working within the hour.
        logger.error("Google OAuth returned no refresh token; nothing was stored")
        raise GoogleOAuthError(
            "Google did not return a refresh token, so the app could not stay "
            "connected. Remove this app's access at "
            "https://myaccount.google.com/permissions and authorize again. "
            "Previously stored credentials were left unchanged."
        )
    try:
        version = save_credentials(credentials)
    except GoogleTokenStoreError as exc:
        logger.error(
            "Google OAuth succeeded but the credentials could not be stored; nothing was saved"
        )
        raise GoogleTokenStoreError(
            f"Google OAuth succeeded but the credentials could not be saved: {exc} "
            "Nothing was stored. Fix the database configuration, then authorize "
            "again via GET /auth/google/authorize."
        ) from exc
    logger.info("Google OAuth completed; credentials saved to the database (version %d)", version)
    return credentials


def _credentials_to_info(credentials: Credentials) -> dict[str, Any]:
    """The authorized-user info to store: everything but the client secret."""
    info = json.loads(credentials.to_json())
    info.pop("client_secret", None)
    return info


def _credentials_from_info(info: dict[str, Any]) -> Credentials:
    """Rebuild Credentials from stored info plus GOOGLE_CLIENT_SECRET."""
    client_id = info.get("client_id")
    if client_id and settings.google_client_id and client_id != settings.google_client_id:
        raise GoogleOAuthError(
            "The stored Google credentials belong to a different OAuth client "
            "than GOOGLE_CLIENT_ID. Authorize again via GET /auth/google/authorize."
        )
    if not info.get("refresh_token"):
        raise GoogleOAuthError(
            "The stored Google credentials have no refresh token. Authorize "
            "again via GET /auth/google/authorize."
        )
    try:
        return Credentials.from_authorized_user_info(
            {**info, "client_secret": settings.google_client_secret}, SCOPES
        )
    except (ValueError, KeyError) as exc:
        raise GoogleOAuthError(
            "The stored Google credentials are not usable. Authorize again "
            "via GET /auth/google/authorize."
        ) from exc


def _store_error(exc: token_store.TokenStoreError) -> GoogleTokenStoreError:
    return GoogleTokenStoreError(str(exc))


def save_credentials(credentials: Credentials) -> int:
    """Store a NEW authorization in the database, replacing any stored one.

    Returns the stored version. Raises GoogleTokenStoreError when the
    database is not configured or the write fails — there is deliberately no
    fallback to a local file.
    """
    global _cache
    with _credentials_lock:
        try:
            version = token_store.save(_credentials_to_info(credentials))
        except token_store.TokenStoreError as exc:
            raise _store_error(exc) from exc
        _cache = _CacheEntry(credentials, version, time.monotonic())
    return version


def _persist_refreshed(credentials: Credentials) -> None:
    """Save a refreshed access token without overwriting newer credentials.

    Only the cached credentials are saved (their stored version is known),
    and only if the row still has that version: when another instance or a
    new authorization wrote meanwhile, the newer row wins and is reloaded.
    A database failure keeps the new access token in memory — the stored
    refresh token is still valid — and the save is retried at the next
    database check. Caller holds ``_credentials_lock``.
    """
    entry = _cache
    if entry is None or entry.credentials is not credentials:
        logger.info("Refreshed Google credentials are no longer the current ones; not saving them")
        return
    try:
        version = token_store.save_if_version(_credentials_to_info(credentials), entry.version)
    except token_store.TokenStoreError:
        entry.unsaved = True
        logger.warning(
            "Refreshed Google access token could not be saved to the database; "
            "continuing with the in-memory token (the stored refresh token is "
            "unchanged) and retrying the save at the next database check"
        )
        return
    entry.unsaved = False
    if version is None:
        logger.warning(
            "Google credentials changed in the database during a token refresh "
            "(new authorization, logout, or another instance); not overwriting them"
        )
        entry.checked_at = float("-inf")  # reload on the next load_credentials()
        return
    entry.version = version
    logger.info("Refreshed Google access token saved to the database (version %d)", version)


def refresh_credentials_if_needed(credentials: Credentials) -> None:
    """Refresh an expired access token when a refresh token is available.

    The refreshed token is saved to the database (see _persist_refreshed).
    Raises the underlying refresh error when refreshing fails.
    """
    with _credentials_lock:
        if credentials.expired and credentials.refresh_token:
            logger.info("Google access token expired; refreshing it")
            credentials.refresh(Request())
            _persist_refreshed(credentials)


def force_refresh_credentials(credentials: Credentials) -> bool:
    """Force a token refresh (used when Google answers 401).

    Returns True when a new access token was obtained.
    """
    if not credentials.refresh_token:
        return False
    try:
        with _credentials_lock:
            credentials.refresh(Request())
            _persist_refreshed(credentials)
        logger.info("Google access token force-refreshed")
        return True
    except Exception as exc:
        logger.error("Forced Google token refresh failed: %s", exc)
        return False


def clear_credentials_cache() -> None:
    """Drop the in-memory credentials (logout, tests)."""
    global _cache
    with _credentials_lock:
        _cache = None


def load_credentials() -> Credentials:
    """Load usable Google credentials (database-backed, cached in memory).

    Raises GoogleOAuthError when the OAuth flow has not been completed or the
    stored credentials can no longer be used, and GoogleTokenStoreError when
    the database is not configured or unreachable (and nothing is cached).

    The cached credentials are reused between database checks, so a
    refreshed access token is used until it expires. An expired access token
    is never a reason to re-authenticate: it is refreshed with the stored
    refresh token and the result saved.
    """
    with _credentials_lock:
        entry = _cache
        now = time.monotonic()
        if entry is None or now - entry.checked_at >= CACHE_RECHECK_SECONDS:
            entry = _sync_with_database(entry, now)
        return _ensure_usable(entry)


def _sync_with_database(entry: _CacheEntry | None, now: float) -> _CacheEntry:
    """Reconcile the cache with the stored row (lock held)."""
    global _cache
    try:
        stored = token_store.load()
    except token_store.TokenStoreError as exc:
        if entry is not None:
            # Keep working through a database outage with the credentials
            # already in memory; check again after the normal interval.
            logger.warning(
                "Credential database unavailable; continuing with the in-memory Google credentials"
            )
            entry.checked_at = now
            return entry
        raise _store_error(exc) from exc
    if stored is None:
        _cache = None
        raise GoogleOAuthError(
            "Google OAuth has not been completed yet: no Google credentials are "
            "stored in the database. Complete the flow via GET /auth/google/authorize."
        )
    if entry is not None and entry.version == stored.version:
        entry.checked_at = now
        if entry.unsaved:
            _persist_refreshed(entry.credentials)
        return entry
    try:
        credentials = _credentials_from_info(stored.info)
    except GoogleOAuthError:
        _cache = None
        raise
    _cache = _CacheEntry(credentials, stored.version, now)
    return _cache


def _ensure_usable(entry: _CacheEntry) -> Credentials:
    """Refresh if needed and return the cached credentials (lock held)."""
    credentials = entry.credentials
    try:
        refresh_credentials_if_needed(credentials)
    except RefreshError as exc:
        logger.error("Google token refresh failed: %s", exc)
        # Maybe re-authorized elsewhere: re-read the database next time. The
        # stored credentials are never deleted automatically.
        entry.checked_at = float("-inf")
        raise GoogleOAuthError(
            "The stored Google credentials could not be refreshed: the refresh "
            "token was revoked or has expired. Authorize again via "
            "GET /auth/google/authorize."
        ) from exc
    except Exception as exc:
        logger.error("Google token refresh failed: %s", exc)
        raise GoogleOAuthError(
            "The Google access token expired and could not be refreshed "
            f"({type(exc).__name__}). This is usually temporary; try again shortly."
        ) from exc
    if not credentials.token:
        raise GoogleOAuthError(
            "The stored Google credentials are not usable. Authorize again "
            "via GET /auth/google/authorize."
        )
    return credentials


def token_storage_diagnostics() -> dict[str, Any]:
    """Safe facts about the credential store — never credential values."""
    info: dict[str, Any] = {
        "token_store": "postgresql",
        "database_configured": token_store.database_configured(),
        "encryption_key_configured": token_store.encryption_key_configured(),
    }
    if settings.google_token_file.strip():
        # Kept compatible, but the file is no longer read or written.
        info["legacy_token_file_ignored"] = True
    try:
        metadata = token_store.metadata()
    except token_store.TokenStoreError as exc:
        if info["database_configured"] and info["encryption_key_configured"]:
            info["database_reachable"] = False
        info["storage_error"] = str(exc)
        return info
    info["database_reachable"] = True
    info["credentials_stored"] = metadata is not None
    if metadata is not None:
        info["stored_version"] = metadata.version
        info["stored_updated_at"] = (
            metadata.updated_at.isoformat() if metadata.updated_at else None
        )
        info["stored_scopes"] = metadata.scopes.split()
    return info


def _verify_google_api() -> dict[str, Any]:
    """One live, read-only Business Profile call with the stored token."""
    # Imported lazily: app.google.client itself imports this module.
    from app.google.client import GoogleAPIError, get_google_client

    try:
        accounts = get_google_client().list_accounts().get("accounts") or []
    except (GoogleOAuthError, GoogleAPIError) as exc:
        return {"google_api_ok": False, "google_api_error": str(exc)}
    return {"google_api_ok": True, "google_accounts_visible": len(accounts)}


def get_auth_status(verify: bool = False) -> dict[str, Any]:
    """Report whether the app currently holds usable Google credentials.

    Always includes :func:`token_storage_diagnostics`. With ``verify`` a
    live Google Business Profile call confirms the token actually works.
    """
    diagnostics = token_storage_diagnostics()
    try:
        credentials = load_credentials()
    except GoogleOAuthError as exc:
        return {"authenticated": False, "reason": str(exc), **diagnostics}
    entry = _cache
    result: dict[str, Any] = {
        "authenticated": True,
        "expires_at": credentials.expiry.isoformat() if credentials.expiry else None,
        "refresh_token_present": bool(credentials.refresh_token),
        "refreshed_token_unsaved": bool(entry and entry.unsaved),
        **diagnostics,
    }
    if verify:
        result.update(_verify_google_api())
    return result


# ---------------------------------------------------------------------------
# HTTP endpoints (thin wrappers around the functions above)
# ---------------------------------------------------------------------------


@router.get("/status")
def auth_status(
    verify: bool = Query(
        False, description="Also make one read-only Google Business Profile call."
    ),
) -> dict[str, Any]:
    """Whether the app currently holds valid Google credentials.

    Includes safe credential-store diagnostics (database configured and
    reachable, credentials stored, version, last update) — never token values.
    """
    return get_auth_status(verify=verify)


@router.get("/authorize")
def authorize() -> dict[str, str]:
    """Create the Google authorization URL.

    Open the returned ``authorization_url`` in a browser, sign in with the
    Google account that manages the boutique, and approve the
    ``business.manage`` scope. Google then redirects the browser back to
    ``GOOGLE_REDIRECT_URI`` (this app's callback), which completes
    authentication.

    If ``GOOGLE_CLIENT_ID`` does not look like a real Google OAuth client
    ID (e.g. it is still the placeholder from `.env.example`), a
    ``warning`` field is included — Google rejects such URLs with a 400.
    """
    try:
        authorization_url, state = get_authorization_url()
    except GoogleOAuthError as exc:
        raise _oauth_http_error(exc) from exc
    result: dict[str, str] = {"authorization_url": authorization_url, "state": state}
    if not _CLIENT_ID_RE.match(settings.google_client_id or ""):
        result["warning"] = (
            "GOOGLE_CLIENT_ID does not look like a valid Google OAuth client ID "
            "(expected format: 123456789012-abc123def.apps.googleusercontent.com). "
            "Put the real Client ID from Google Cloud Console (APIs & Services -> "
            "Credentials -> your OAuth client) into .env, restart the server, and "
            "request this URL again — Google rejects placeholder client IDs with a 400."
        )
    return result


@router.get("/callback")
def callback(
    code: str = Query(..., description="Authorization code returned by Google."),
    state: str = Query("", description="State value returned by Google."),
) -> dict[str, Any]:
    """OAuth callback: exchange the code for credentials and store them."""
    try:
        complete_authorization(code, state)
    except GoogleOAuthError as exc:
        raise _oauth_http_error(exc) from exc
    return {
        "authenticated": True,
        "message": (
            "Google OAuth completed. You can now fetch unanswered reviews "
            "with GET /reviews."
        ),
    }


@router.post("/logout")
def logout() -> dict[str, Any]:
    """Disconnect the Google account by deleting the stored credentials."""
    global _current_state, _current_flow
    _current_state = None
    _current_flow = None
    with _credentials_lock:
        try:
            existed = token_store.delete()
        except token_store.TokenStoreError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Could not disconnect the Google account. {exc}",
            ) from exc
        finally:
            clear_credentials_cache()
    logger.info("Google account disconnected (stored credentials removed=%s)", existed)
    return {"authenticated": False, "message": "Google account disconnected."}


def _oauth_http_error(exc: GoogleOAuthError) -> HTTPException:
    if isinstance(exc, (GoogleOAuthNotConfiguredError, GoogleTokenStoreError)):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
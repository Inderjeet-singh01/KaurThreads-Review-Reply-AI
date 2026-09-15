"""Google OAuth 2.0 authentication for the Business Profile APIs.

This module ONLY handles OAuth authentication/authorization:

* creating the Google authorization URL,
* the required OAuth scopes,
* the OAuth callback (authorization code -> credentials),
* loading / saving / refreshing the token in ``credentials/google_token.json``.

It never fetches reviews and never publishes replies.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import (
    GOOGLE_AUTH_URL,
    GOOGLE_SCOPE_BUSINESS_MANAGE,
    GOOGLE_TOKEN_FILE,
    GOOGLE_TOKEN_URL,
    settings,
)

logger = logging.getLogger(__name__)

# The single Business Profile management scope required by this application.
SCOPES = [GOOGLE_SCOPE_BUSINESS_MANAGE]

router = APIRouter(prefix="/auth/google", tags=["Google OAuth"])

# Single-user local tool: remember the OAuth ``state`` of the in-progress
# flow so the callback can be validated against it.
_current_state: str | None = None


class GoogleOAuthError(Exception):
    """Google OAuth is missing, unusable, or failed."""


class GoogleOAuthNotConfiguredError(GoogleOAuthError):
    """OAuth client settings are not present in the environment / .env."""


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
        include_granted_scopes=True,
    )
    global _current_state
    _current_state = state
    logger.info("Google authorization URL created")
    return authorization_url, state


def complete_authorization(code: str, state: str) -> Credentials:
    """Exchange the authorization code for credentials and save them.

    Raises GoogleOAuthError when the state does not match or Google rejects
    the code. On success the token file is written.
    """
    flow = _build_flow()
    if _current_state is not None and state != _current_state:
        logger.warning("OAuth callback state did not match the started flow")
        raise GoogleOAuthError(
            "OAuth state mismatch. Open the authorization URL again and "
            "complete the flow."
        )
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # network error or invalid_code from Google
        logger.error("Failed to exchange OAuth code for credentials: %s", exc)
        raise GoogleOAuthError(
            "Google rejected the authorization code. Please retry the "
            "authorization flow."
        ) from exc
    save_credentials(flow.credentials)
    logger.info("Google OAuth completed; token saved to %s", GOOGLE_TOKEN_FILE)
    return flow.credentials


def save_credentials(credentials: Credentials) -> None:
    """Persist the OAuth token (including refresh token) to the token file."""
    GOOGLE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GOOGLE_TOKEN_FILE, "w", encoding="utf-8") as token_file:
        token_file.write(credentials.to_json())
    try:
        os.chmod(GOOGLE_TOKEN_FILE, 0o600)  # best effort (non-POSIX safe)
    except OSError:
        pass


def refresh_credentials_if_needed(credentials: Credentials) -> None:
    """Refresh an expired access token when a refresh token is available.

    The refreshed token is persisted so the next process start benefits from
    it. Raises the underlying refresh error when refreshing fails.
    """
    if credentials.expired and credentials.refresh_token:
        logger.info("Google access token expired; refreshing it")
        credentials.refresh(Request())
        save_credentials(credentials)


def force_refresh_credentials(credentials: Credentials) -> bool:
    """Force a token refresh (used when Google answers 401).

    Returns True when a new access token was obtained.
    """
    if not credentials.refresh_token:
        return False
    try:
        credentials.refresh(Request())
        save_credentials(credentials)
        logger.info("Google access token force-refreshed")
        return True
    except Exception as exc:
        logger.error("Forced Google token refresh failed: %s", exc)
        return False


def load_credentials() -> Credentials:
    """Load usable Google credentials from ``credentials/google_token.json``.

    Raises GoogleOAuthError when the OAuth flow has not been completed or the
    stored credentials can no longer be used.
    """
    if not GOOGLE_TOKEN_FILE.exists():
        raise GoogleOAuthError(
            "Google OAuth has not been completed yet: no token file at "
            f"{GOOGLE_TOKEN_FILE}. Complete the flow via "
            "GET /auth/google/authorize."
        )
    try:
        credentials = Credentials.from_authorized_user_file(
            str(GOOGLE_TOKEN_FILE), SCOPES
        )
    except (OSError, ValueError, KeyError) as exc:
        raise GoogleOAuthError(
            f"Could not read the Google credentials file at {GOOGLE_TOKEN_FILE}. "
            "Delete the file and complete the OAuth flow again."
        ) from exc
    try:
        refresh_credentials_if_needed(credentials)
    except Exception as exc:
        logger.error("Google token refresh failed: %s", exc)
        raise GoogleOAuthError(
            "The stored Google credentials have expired and could not be "
            "refreshed automatically. Delete credentials/google_token.json "
            "and complete the OAuth flow again."
        ) from exc
    if not credentials.token:
        raise GoogleOAuthError(
            "The stored Google credentials are not usable. Delete "
            "credentials/google_token.json and complete the OAuth flow again "
            "via GET /auth/google/authorize."
        )
    return credentials


def get_auth_status() -> dict[str, Any]:
    """Report whether the app currently holds usable Google credentials."""
    if not GOOGLE_TOKEN_FILE.exists():
        return {
            "authenticated": False,
            "reason": (
                "OAuth not completed (credentials/google_token.json does not "
                "exist yet)."
            ),
        }
    try:
        credentials = load_credentials()
    except GoogleOAuthError as exc:
        return {"authenticated": False, "reason": str(exc)}
    return {
        "authenticated": True,
        "expires_at": credentials.expiry.isoformat() if credentials.expiry else None,
        "token_file": str(GOOGLE_TOKEN_FILE),
    }


# ---------------------------------------------------------------------------
# HTTP endpoints (thin wrappers around the functions above)
# ---------------------------------------------------------------------------


@router.get("/status")
def auth_status() -> dict[str, Any]:
    """Whether the app currently holds valid Google credentials."""
    return get_auth_status()


@router.get("/authorize")
def authorize() -> dict[str, str]:
    """Create the Google authorization URL.

    Open the returned ``authorization_url`` in a browser, sign in with the
    Google account that manages the boutique, and approve the
    ``business.manage`` scope. Google then redirects the browser back to
    ``GOOGLE_REDIRECT_URI`` (this app's callback), which completes
    authentication.
    """
    try:
        authorization_url, state = get_authorization_url()
    except GoogleOAuthError as exc:
        raise _oauth_http_error(exc) from exc
    return {"authorization_url": authorization_url, "state": state}


@router.get("/callback")
def callback(
    code: str = Query(..., description="Authorization code returned by Google."),
    state: str = Query("", description="State value returned by Google."),
) -> dict[str, str]:
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


def _oauth_http_error(exc: GoogleOAuthError) -> HTTPException:
    if isinstance(exc, GoogleOAuthNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))

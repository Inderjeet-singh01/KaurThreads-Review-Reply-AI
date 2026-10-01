"""Google OAuth 2.0 authentication for the Business Profile APIs.

This module ONLY handles OAuth authentication/authorization:

* creating the Google authorization URL,
* the required OAuth scopes,
* the OAuth callback (authorization code -> credentials),
* loading / saving / refreshing the token in ``GOOGLE_TOKEN_FILE`` (default
  ``credentials/google_token.json``; see :mod:`app.config`).

It never fetches reviews and never publishes replies.
"""

from __future__ import annotations

import json
import logging
import os
import re
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
    the code. On success the token file is written.
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
    try:
        save_credentials(_current_flow.credentials)
    except OSError as exc:
        logger.error(
            "Could not write the Google token file at %s (%s)",
            GOOGLE_TOKEN_FILE, type(exc).__name__,
        )
        raise GoogleOAuthError(
            f"Google OAuth succeeded but the token could not be saved to "
            f"{GOOGLE_TOKEN_FILE} ({type(exc).__name__}). If GOOGLE_TOKEN_FILE "
            "points at a read-only Render Secret File, upload the token there "
            "instead of running the OAuth flow in production."
        ) from exc
    logger.info("Google OAuth completed; token saved to %s", GOOGLE_TOKEN_FILE)
    return _current_flow.credentials


def save_credentials(credentials: Credentials) -> None:
    """Persist the OAuth token (including refresh token) to the token file."""
    GOOGLE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GOOGLE_TOKEN_FILE, "w", encoding="utf-8") as token_file:
        token_file.write(credentials.to_json())
    try:
        os.chmod(GOOGLE_TOKEN_FILE, 0o600)  # best effort (non-POSIX safe)
    except OSError:
        pass


def _persist_refreshed(credentials: Credentials) -> None:
    """Best-effort save after a refresh.

    The refresh token is unchanged by a refresh, so a read-only token file
    (e.g. a Render Secret File) keeps working: the new access token simply
    lives in memory and is refreshed again when needed.
    """
    try:
        save_credentials(credentials)
    except OSError as exc:
        logger.warning(
            "Refreshed Google access token could not be written to %s (%s); "
            "continuing with the in-memory token",
            GOOGLE_TOKEN_FILE, type(exc).__name__,
        )


def refresh_credentials_if_needed(credentials: Credentials) -> None:
    """Refresh an expired access token when a refresh token is available.

    The refreshed token is persisted (best effort) so the next process start
    benefits from it. Raises the underlying refresh error when refreshing
    fails.
    """
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
        credentials.refresh(Request())
        _persist_refreshed(credentials)
        logger.info("Google access token force-refreshed")
        return True
    except Exception as exc:
        logger.error("Forced Google token refresh failed: %s", exc)
        return False


def load_credentials() -> Credentials:
    """Load usable Google credentials from ``GOOGLE_TOKEN_FILE``.

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
            f"refreshed automatically. Replace {GOOGLE_TOKEN_FILE} by "
            "completing the OAuth flow again."
        ) from exc
    if not credentials.token:
        raise GoogleOAuthError(
            f"The stored Google credentials in {GOOGLE_TOKEN_FILE} are not "
            "usable. Replace the file by completing the OAuth flow again "
            "via GET /auth/google/authorize."
        )
    return credentials


def _credential_type(data: Any) -> str:
    """Classify a token file by its keys only (values are never inspected)."""
    if not isinstance(data, dict):
        return "unknown"
    if "web" in data or "installed" in data:
        # The OAuth client secret downloaded from Google Cloud Console — not
        # a user token; it cannot authenticate API calls.
        return "oauth_client_config"
    if data.get("type") == "service_account":
        return "service_account"
    if data.get("refresh_token") or data.get("type") == "authorized_user":
        return "authorized_user"
    return "unknown"


def token_file_diagnostics() -> dict[str, Any]:
    """Safe metadata about the configured token file — never its contents.

    Reports where the token is expected, why (GOOGLE_TOKEN_FILE or default),
    and whether the file exists, is readable, and looks like a user token.
    """
    path = GOOGLE_TOKEN_FILE
    exists = path.is_file()
    info: dict[str, Any] = {
        "configured_token_path": str(path),
        "token_path_source": (
            "GOOGLE_TOKEN_FILE" if settings.google_token_file.strip() else "default"
        ),
        "env_variable_set": bool(os.environ.get("GOOGLE_TOKEN_FILE", "").strip()),
        "file_exists": exists,
        "file_readable": exists and os.access(path, os.R_OK),
    }
    if info["file_readable"]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info["credential_type"] = "invalid_json"
        else:
            info["credential_type"] = _credential_type(data)
            info["refresh_token_present"] = bool(
                isinstance(data, dict) and data.get("refresh_token")
            )
    elif not exists:
        # Helps spot a Secret File uploaded under a different name. Only file
        # NAMES containing "token" are listed, never contents.
        parent = path.parent
        info["parent_dir_exists"] = parent.is_dir()
        if info["parent_dir_exists"]:
            try:
                info["similar_files_in_dir"] = sorted(
                    entry.name for entry in parent.iterdir()
                    if "token" in entry.name.lower()
                )[:10]
            except OSError:
                pass
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

    Always includes :func:`token_file_diagnostics`. With ``verify`` a live
    Google Business Profile call confirms the token actually works.
    """
    diagnostics = token_file_diagnostics()
    if not diagnostics["file_exists"]:
        return {
            "authenticated": False,
            "reason": f"OAuth not completed (no token file at {GOOGLE_TOKEN_FILE}).",
            **diagnostics,
        }
    try:
        credentials = load_credentials()
    except GoogleOAuthError as exc:
        return {"authenticated": False, "reason": str(exc), **diagnostics}
    result: dict[str, Any] = {
        "authenticated": True,
        "expires_at": credentials.expiry.isoformat() if credentials.expiry else None,
        "token_file": str(GOOGLE_TOKEN_FILE),
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

    Includes safe token-file diagnostics (path, existence, readability,
    credential type) — never token values.
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
    """Disconnect the Google account by deleting the stored token file."""
    global _current_state, _current_flow
    _current_state = None
    _current_flow = None
    existed = GOOGLE_TOKEN_FILE.exists()
    if existed:
        try:
            GOOGLE_TOKEN_FILE.unlink()
        except OSError as exc:
            logger.error("Failed to delete token file: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="Could not disconnect the Google account. Try again.",
            ) from exc
    logger.info("Google account disconnected (token file removed=%s)", existed)
    return {"authenticated": False, "message": "Google account disconnected."}


def _oauth_http_error(exc: GoogleOAuthError) -> HTTPException:
    if isinstance(exc, GoogleOAuthNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
"""Central application configuration.

All secrets are read from environment variables (optionally via a local
`.env` file). Nothing sensitive is hardcoded, and the Google OAuth token is
never stored in `.env` — it lives, encrypted, in the PostgreSQL database
named by DATABASE_URL (see app/auth/token_store.py).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the directory that contains the `app/` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default location of the legacy token file (gitignored). Only used as the
# import source of `python -m app.auth.import_token`.
CREDENTIALS_DIR = PROJECT_ROOT / "credentials"

# Google Business Profile management scope required by this application.
# A single token with this scope works across the three Business Profile
# APIs used here (Reviews v4, Account Management v1, Business Information v1).
GOOGLE_SCOPE_BUSINESS_MANAGE = "https://www.googleapis.com/auth/business.manage"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class Settings(BaseSettings):
    """Runtime settings loaded from the environment / `.env` file."""

    # --- Google OAuth 2.0 -------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # --- Google Business Profile location (optional) ----------------------
    # When set, only this location is used (either the bare location ID or
    # the full resource name accounts/{account}/locations/{location}).
    # When empty, the first location of the first My Business account is
    # auto-resolved through the Google Business Profile APIs.
    google_location_id: str = ""

    # --- Groq -------------------------------------------------------------
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Gemini (automatic fallback when Groq generation fails) ------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

    # --- Google OAuth credential storage (PostgreSQL, e.g. Neon) -----------
    # Connection string of the database that stores the OAuth credentials.
    # Required: without it OAuth cannot complete and Google calls fail.
    database_url: str = ""
    # Fernet key(s) encrypting the stored credentials. Comma-separate several
    # keys to rotate (the first encrypts, all decrypt). Required.
    google_token_encryption_key: str = ""
    # Legacy token file. No longer read or written at runtime; only the
    # default source path of `python -m app.auth.import_token`.
    google_token_file: str = ""

    # --- Automatic review replies (Pub/Sub webhook) ------------------------
    # Master switch. While false, new-review events are received and logged
    # but never processed by AI and NEVER published.
    auto_reply_enabled: bool = False
    # When true (and enabled), the full pipeline runs but publish_reply() is
    # never called; the run is logged as WOULD_PUBLISH instead.
    auto_reply_dry_run: bool = False
    # Validation-triggered regenerations per review. Hard upper bound of 1.
    auto_reply_max_regenerations: int = Field(default=1, ge=0, le=1)
    # Optional comma-separated allowlist of location ids to automate. Empty =
    # every location of the account the notification setting belongs to.
    auto_reply_location_ids: str = ""
    # Authenticated Pub/Sub push: the expected JWT audience (the value set as
    # --push-auth-token-audience, or the push endpoint URL by default) and the
    # expected service-account email in the token. Both are required; the
    # webhook rejects every request while either is empty.
    pubsub_push_audience: str = ""
    pubsub_push_service_account: str = ""
    # Development-only POST /automation/test/{review_id}. Keep false in production.
    automation_test_endpoint_enabled: bool = False
    # Bulk "reply to all pending reviews" (POST /automation/backfill):
    # pause between two reviews of a backfill (Groq / Google rate limits).
    automation_backfill_delay_seconds: float = Field(default=2.0, ge=0, le=60)
    # Base wait before retrying a review after a transient failure (AI rate
    # limit, Google 5xx); multiplied by the attempt number. AI quotas are per
    # minute, so a short pause would just fail again.
    automation_backfill_retry_delay_seconds: float = Field(default=15.0, ge=0, le=300)
    # After a successful publish, re-read the review once to confirm Google
    # shows the reply (logged; a failed check never undoes PUBLISHED).
    auto_reply_verify_after_publish: bool = True

    # --- Reconciliation (safety net for delayed / missed Pub/Sub events) ---
    # POST /automation/reconcile, called by an external scheduler. It finds
    # recent unanswered reviews and runs each through process_new_review().
    reconciliation_enabled: bool = True
    # Shared secret the scheduler sends (Authorization: Bearer <secret> or
    # X-Reconcile-Secret). Empty / shorter than 16 chars = endpoint closed.
    reconciliation_secret: str = ""
    # Reviews processed per reconciliation run (newest first).
    reconciliation_max_reviews: int = Field(default=10, ge=1, le=100)
    # Only reviews created/updated within this window are candidates, for
    # reconciliation AND for the webhook's fallback resolution. 0 = no limit
    # (every unanswered review, like the bulk backfill). Default: 7 days,
    # Pub/Sub's default message retention.
    reconciliation_lookback_minutes: int = Field(default=7 * 24 * 60, ge=0)

    @property
    def reconciliation_auth_configured(self) -> bool:
        return len(self.reconciliation_secret.strip()) >= 16

    @property
    def auto_reply_location_list(self) -> list[str]:
        """Parsed AUTO_REPLY_LOCATION_IDS (bare location ids)."""
        return [
            value.strip().rstrip("/").rsplit("/", 1)[-1]
            for value in self.auto_reply_location_ids.split(",")
            if value.strip()
        ]

    # --- Frontend / CORS --------------------------------------------------
    # Comma-separated list of browser origins allowed to call this API.
    # Defaults cover the local Vite dev server. Set CORS_ALLOW_ORIGINS in
    # .env for other hosts (e.g. a deployed frontend URL).
    cors_allow_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed list of allowed CORS origins."""
        return [
            origin.strip()
            for origin in self.cors_allow_origins.split(",")
            if origin.strip()
        ]

    # Base URL of the deployed frontend (e.g. https://your-site.netlify.app).
    # After Google sign-in the OAuth callback redirects the browser here. The
    # destination is never taken from the request, so the callback cannot be
    # used as an open redirect. Empty = the callback answers JSON (API-only use).
    frontend_url: str = ""

    @property
    def frontend_redirect_base(self) -> str:
        """FRONTEND_URL without a trailing slash, or "" when unset or invalid.

        Only an absolute http(s) URL with a host and no query, fragment or
        user info is accepted.
        """
        value = self.frontend_url.strip().strip("'\"").strip().rstrip("/")
        if not value:
            return ""
        try:
            parts = urlsplit(value)
        except ValueError:
            return ""
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or "@" in parts.netloc
            or parts.query
            or parts.fragment
        ):
            return ""
        return value

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def resolve_google_token_file(configured: str) -> Path:
    """The legacy token file path: GOOGLE_TOKEN_FILE when set, else the default.

    Surrounding whitespace/quotes (easy to paste into a dashboard) are
    ignored, and a relative path is anchored at the project root rather than
    the process working directory.
    """
    value = configured.strip().strip("'\"").strip()
    if not value:
        return CREDENTIALS_DIR / "google_token.json"
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


settings = Settings()

# The legacy token file path (import source only; see google_token_file).
GOOGLE_TOKEN_FILE = resolve_google_token_file(settings.google_token_file)

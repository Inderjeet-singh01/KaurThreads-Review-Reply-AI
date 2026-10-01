"""Central application configuration.

All secrets are read from environment variables (optionally via a local
`.env` file). Nothing sensitive is hardcoded, and the Google OAuth token is
never stored in `.env` — it lives in GOOGLE_TOKEN_FILE (default
`credentials/google_token.json`).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the directory that contains the `app/` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Where the Google OAuth token is persisted by default. This directory is
# gitignored. GOOGLE_TOKEN_FILE (see Settings) can point elsewhere, e.g. a
# persistent disk in production — resolved at the bottom of this module.
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

    # --- Google OAuth token location (optional) ----------------------------
    # Absolute path of the OAuth token file. Empty = credentials/google_token.json.
    # Render's default filesystem is ephemeral, so production automation needs
    # this on a persistent disk (or a Render Secret File) — see README.
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

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def resolve_google_token_file(configured: str) -> Path:
    """The OAuth token path: GOOGLE_TOKEN_FILE when set, else the default.

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

# The single canonical token path. Every reader/writer (OAuth callback,
# status, load_credentials -> Google client -> automation) uses this value.
GOOGLE_TOKEN_FILE = resolve_google_token_file(settings.google_token_file)

"""Central application configuration.

All secrets are read from environment variables (optionally via a local
`.env` file). Nothing sensitive is hardcoded, and the Google OAuth token is
never stored in `.env` — it lives in `credentials/google_token.json`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the directory that contains the `app/` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Where the Google OAuth token is persisted. This directory is gitignored.
CREDENTIALS_DIR = PROJECT_ROOT / "credentials"
GOOGLE_TOKEN_FILE = CREDENTIALS_DIR / "google_token.json"

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

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()

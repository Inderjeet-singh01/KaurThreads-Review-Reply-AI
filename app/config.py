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

    # --- Gemini (automatic fallback when Groq generation fails) ------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

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


settings = Settings()

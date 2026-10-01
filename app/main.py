"""Google Review Reply AI — FastAPI application entry point.

Creates the FastAPI application and registers routers. All business logic
lives in app/api, app/auth, app/google, and app/ai.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.locations import router as locations_router
from app.api.reviews import router as reviews_router
from app.auth.google_oauth import router as google_auth_router
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
# Hide per-request httpx lines from the AI SDKs; reply_generator logs outcomes.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Startup validation: fail fast when required env vars are missing
# ---------------------------------------------------------------------------
_REQUIRED_SETTINGS = {
    "GOOGLE_CLIENT_ID": settings.google_client_id,
    "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
    "GOOGLE_REDIRECT_URI": settings.google_redirect_uri,
}
_missing = [name for name, value in _REQUIRED_SETTINGS.items() if not value]
# Each AI provider is optional on its own (Gemini is the fallback for Groq),
# but at least one must be configured for reply generation to work.
if not settings.groq_api_key and not settings.gemini_api_key:
    _missing.append("GROQ_API_KEY or GEMINI_API_KEY")
if _missing:
    raise RuntimeError(
        "Missing required environment variables: "
        f"{', '.join(_missing)}. Set them in the .env file and restart."
    )
if not settings.groq_api_key:
    logger.warning("GROQ_API_KEY is not set; replies will be generated with Gemini only")
if not settings.gemini_api_key:
    logger.warning(
        "GEMINI_API_KEY is not set; there is no fallback if Groq fails"
    )

app = FastAPI(
    title="Google Review Reply AI",
    version="1.0.0",
    description=(
        "Phase 1: fetch unanswered Google Business Profile reviews for a "
        "boutique, generate professional replies with Groq (Gemini "
        "fallback), and publish them only after explicit user approval "
        "plus a final reply check on "
        "Google. Generated replies are NEVER published automatically."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(google_auth_router)
app.include_router(reviews_router)
app.include_router(locations_router)


@app.get("/", tags=["Health"])
def root() -> dict[str, str]:
    """Basic app info."""
    return {
        "app": "Google Review Reply AI",
        "phase": "1",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}

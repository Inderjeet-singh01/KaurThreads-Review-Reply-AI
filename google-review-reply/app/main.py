"""Google Review Reply AI — FastAPI application entry point.

Creates the FastAPI application and registers routers. All business logic
lives in app/api, app/auth, app/google, and app/ai.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.reviews import router as reviews_router
from app.auth.google_oauth import router as google_auth_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Google Review Reply AI",
    version="1.0.0",
    description=(
        "Phase 1: fetch unanswered Google Business Profile reviews for a "
        "boutique, generate professional replies with Groq, and publish them "
        "only after explicit user approval plus a final reply check on "
        "Google. Generated replies are NEVER published automatically."
    ),
)

app.include_router(google_auth_router)
app.include_router(reviews_router)


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

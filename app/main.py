"""Google Review Reply AI — FastAPI application entry point.

Creates the FastAPI application and registers routers. All business logic
lives in app/api, app/auth, app/google, and app/ai.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.automation import router as automation_router
from app.api.locations import router as locations_router
from app.api.reviews import router as reviews_router
from app import timing
from app.auth import token_store
from app.auth.google_oauth import router as google_auth_router
from app.config import settings
from app.webhooks.google_reviews import router as google_reviews_webhook_router
from app.webhooks.pubsub import pubsub_auth_configured

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

logger.info(
    "Automatic replies: enabled=%s dry_run=%s max_regenerations=%d "
    "locations=%s webhook_auth_configured=%s token_store=postgresql "
    "database_configured=%s encryption_key_configured=%s",
    settings.auto_reply_enabled, settings.auto_reply_dry_run,
    settings.auto_reply_max_regenerations,
    settings.auto_reply_location_list or "all", pubsub_auth_configured(),
    token_store.database_configured(), token_store.encryption_key_configured(),
)
try:
    token_store.check_configuration()
except token_store.TokenStoreError as exc:
    # Not fatal: /health keeps answering and /auth/google/status reports it.
    logger.error("Google credential storage is not usable: %s", exc)
if settings.google_token_file.strip():
    logger.warning(
        "GOOGLE_TOKEN_FILE is set but no longer used: Google credentials are stored "
        "in the database. Import an existing token once with "
        "`python -m app.auth.import_token`, then remove GOOGLE_TOKEN_FILE"
    )
if settings.auto_reply_enabled and not pubsub_auth_configured():
    logger.warning(
        "AUTO_REPLY_ENABLED is true but PUBSUB_PUSH_AUDIENCE / "
        "PUBSUB_PUSH_SERVICE_ACCOUNT are not set: the webhook rejects every request"
    )
logger.info(
    "Bulk backfill: delay_seconds=%s", settings.automation_backfill_delay_seconds,
)
logger.info(
    "Reconciliation: enabled=%s auth_configured=%s max_reviews=%d lookback_minutes=%d "
    "verify_after_publish=%s",
    settings.reconciliation_enabled, settings.reconciliation_auth_configured,
    settings.reconciliation_max_reviews, settings.reconciliation_lookback_minutes,
    settings.auto_reply_verify_after_publish,
)
if settings.auto_reply_enabled and settings.reconciliation_enabled \
        and not settings.reconciliation_auth_configured:
    logger.warning(
        "RECONCILIATION_SECRET is not set (16+ characters): POST /automation/reconcile is "
        "closed, so reviews whose Pub/Sub notification is missed are not retried automatically"
    )
if settings.automation_test_endpoint_enabled:
    logger.warning(
        "AUTOMATION_TEST_ENDPOINT_ENABLED is true: POST /automation/test/{review_id} "
        "is exposed. Development only — disable it in production."
    )

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create the credentials table at startup; close the pool on shutdown."""
    try:
        token_store.ensure_schema()
    except token_store.TokenStoreError:
        pass  # already logged; retried on first use
    yield
    token_store.close()


app = FastAPI(
    lifespan=lifespan,
    title="Google Review Reply AI",
    version="1.0.0",
    description=(
        "Fetch unanswered Google Business Profile reviews for a boutique, "
        "generate professional replies with Groq (Gemini fallback), and "
        "publish them after explicit user approval plus a final reply check "
        "on Google. Optional automatic replies for new reviews (Pub/Sub "
        "webhook) are OFF unless AUTO_REPLY_ENABLED=true, and publish only "
        "after validation and a final Google check."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def request_timing(request: Request, call_next):
    """Log each request's duration split into database / Google time.

    Also returned as a Server-Timing header (DevTools -> Network -> Timing),
    so a slow screen can be attributed to Neon, Google or the network.
    Logs the method and path only — no query string, body or credentials.
    """
    recorder, token = timing.begin()
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        timing.end(token)
    total_ms = (time.perf_counter() - start) * 1000
    response.headers["Server-Timing"] = timing.server_timing(recorder, total_ms)
    if request.method != "OPTIONS" and request.url.path != "/health":
        logger.info(
            "request %s %s -> %d in %.0f ms (%s)",
            request.method, request.url.path, response.status_code, total_ms,
            timing.summary(recorder),
        )
    return response


app.include_router(google_auth_router)
app.include_router(reviews_router)
app.include_router(locations_router)
app.include_router(automation_router)
app.include_router(google_reviews_webhook_router)


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

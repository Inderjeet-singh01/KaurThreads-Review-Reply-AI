"""PostgreSQL persistence for the Google OAuth credentials.

The authorized-user credentials (refresh token, access token, expiry, scopes)
live in one row of ``google_oauth_credentials`` in the database named by
``DATABASE_URL`` (Neon in production). Render's filesystem is ephemeral and
its Secret Files are read-only, so the database is the only durable,
writable store: credentials survive restarts and redeploys, and refreshed
tokens are saved.

* **Encryption:** the credential JSON is encrypted with Fernet (AES-128-CBC +
  HMAC-SHA256) using ``GOOGLE_TOKEN_ENCRYPTION_KEY`` before it reaches the
  database. Several comma-separated keys may be given to rotate: the first
  encrypts, all of them decrypt. The OAuth client secret is never stored; it
  is taken from ``GOOGLE_CLIENT_SECRET`` when the credentials are loaded.
* **Concurrency:** every write bumps ``version``. A refreshed token is only
  written if the row still has the version it was loaded from, so a stale
  process can never overwrite a newer authorization or refresh.
* **Schema:** created idempotently (``CREATE TABLE IF NOT EXISTS`` under an
  advisory lock) on startup and before the first query.

Nothing here logs or returns token values, the encryption key, or the
database password.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings

logger = logging.getLogger(__name__)

TABLE = "google_oauth_credentials"
# Single-account app: one row. The key column leaves room for more accounts.
CREDENTIALS_ID = "default"

# Arbitrary constant: serializes concurrent schema creation across processes
# (two instances overlap briefly during a Render deploy).
_SCHEMA_LOCK_ID = 7_301_955_021

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id TEXT PRIMARY KEY,
    encrypted_credentials TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    token_expiry TIMESTAMPTZ,
    scopes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CONNECT_TIMEOUT_SECONDS = 10
# Longest a request waits for a connection (Neon cold starts take a few seconds).
POOL_TIMEOUT_SECONDS = 10
POOL_MAX_SIZE = 4
# Close idle connections quickly so Neon's free compute can auto-suspend.
POOL_MAX_IDLE_SECONDS = 120

_ENCRYPTION_KEY_HELP = (
    'Generate one with: python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())" and set it as '
    "GOOGLE_TOKEN_ENCRYPTION_KEY (Render: Environment Variables)."
)


class TokenStoreError(Exception):
    """The credential database is not configured, unreachable, or failed."""


class TokenStoreNotConfiguredError(TokenStoreError):
    """DATABASE_URL or GOOGLE_TOKEN_ENCRYPTION_KEY is missing or invalid."""


@dataclass(frozen=True)
class StoredCredentials:
    """A decrypted credentials row. ``info`` holds secrets — never log it."""

    info: dict[str, Any]
    version: int
    updated_at: datetime | None


@dataclass(frozen=True)
class StoredMetadata:
    """Non-secret facts about the stored row (for status diagnostics)."""

    version: int
    token_expiry: datetime | None
    scopes: str
    created_at: datetime | None
    updated_at: datetime | None


_lock = threading.Lock()
_pool: Any = None
_pool_url: str | None = None
_schema_ready = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _clean(value: str) -> str:
    """Strip whitespace/quotes that are easy to paste into a dashboard."""
    return value.strip().strip("'\"").strip()


def database_url() -> str:
    return _clean(settings.database_url)


def database_configured() -> bool:
    return bool(database_url())


def encryption_key_configured() -> bool:
    return bool(_clean(settings.google_token_encryption_key))


def _fernet() -> MultiFernet:
    raw = _clean(settings.google_token_encryption_key)
    keys = [key.strip() for key in raw.split(",") if key.strip()]
    if not keys:
        raise TokenStoreNotConfiguredError(
            "GOOGLE_TOKEN_ENCRYPTION_KEY is not set, so Google credentials "
            f"cannot be stored securely. {_ENCRYPTION_KEY_HELP}"
        )
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (ValueError, TypeError) as exc:
        raise TokenStoreNotConfiguredError(
            "GOOGLE_TOKEN_ENCRYPTION_KEY is not a valid Fernet key (expected "
            f"32 url-safe base64-encoded bytes). {_ENCRYPTION_KEY_HELP}"
        ) from exc


def check_configuration() -> None:
    """Raise TokenStoreNotConfiguredError when the store cannot be used."""
    if not database_configured():
        raise TokenStoreNotConfiguredError(
            "DATABASE_URL is not set, so Google credentials cannot be stored "
            "or loaded. Set it to the Neon PostgreSQL connection string "
            "(Render: Environment Variables; locally: .env)."
        )
    from psycopg.conninfo import conninfo_to_dict

    try:
        conninfo_to_dict(database_url())
    except Exception:
        # libpq's parse error can echo the string (password included): drop it.
        raise TokenStoreNotConfiguredError(
            "DATABASE_URL is not a valid PostgreSQL connection string. Copy it "
            "again from the Neon dashboard (Connection Details)."
        ) from None
    _fernet()


# ---------------------------------------------------------------------------
# Safe error text
# ---------------------------------------------------------------------------
_URL_PASSWORD_RE = re.compile(r"(://[^:/@\s]*:)[^@\s]*@")
_KV_PASSWORD_RE = re.compile(r"(password\s*=\s*)('[^']*'|\S+)", re.IGNORECASE)


def redact(text: str) -> str:
    """Remove passwords from driver messages (libpq can echo a bad DSN)."""
    text = _URL_PASSWORD_RE.sub(r"\1***@", text)
    return _KV_PASSWORD_RE.sub(r"\1***", text)


def _db_error(action: str, exc: Exception) -> TokenStoreError:
    detail = redact(str(exc).strip().splitlines()[0] if str(exc).strip() else "")[:200]
    logger.error(
        "Credential database error while %s (%s): %s", action, type(exc).__name__, detail
    )
    return TokenStoreError(
        f"The credential database could not be reached while {action} "
        f"({type(exc).__name__}). Check DATABASE_URL and that the Neon "
        "project is active; details are in the server logs."
    )


# ---------------------------------------------------------------------------
# Connection pool + schema
# ---------------------------------------------------------------------------
def _get_pool():
    global _pool, _pool_url, _schema_ready
    check_configuration()
    url = database_url()
    with _lock:
        if _pool is not None and _pool_url == url:
            return _pool
        if _pool is not None:
            _pool.close()
            _schema_ready = False
        from psycopg_pool import ConnectionPool

        try:
            _pool = ConnectionPool(
                url,
                min_size=0,
                max_size=POOL_MAX_SIZE,
                max_idle=POOL_MAX_IDLE_SECONDS,
                timeout=POOL_TIMEOUT_SECONDS,
                # Neon closes connections when its compute auto-suspends:
                # check each one before handing it out.
                check=ConnectionPool.check_connection,
                # prepare_threshold=None: no server-side prepared statements,
                # which Neon's PgBouncer (-pooler host) may not support.
                kwargs={"connect_timeout": CONNECT_TIMEOUT_SECONDS, "prepare_threshold": None},
                open=False,
                name="google-oauth-credentials",
            )
            _pool.open(wait=False)
        except Exception as exc:
            _pool = None
            raise _db_error("opening the connection pool", exc) from exc
        _pool_url = url
        return _pool


def _run(action: str, sql: str, params: tuple = ()):
    """Execute one statement in its own transaction; return the first row."""
    pool = _get_pool()
    ensure_schema()
    try:
        with pool.connection() as conn:
            return conn.execute(sql, params).fetchone()
    except Exception as exc:
        raise _db_error(action, exc) from exc


def ensure_schema() -> None:
    """Create the credentials table if needed (idempotent, multi-process safe)."""
    global _schema_ready
    if _schema_ready:
        return
    pool = _get_pool()
    try:
        with pool.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_ID,))
            conn.execute(_SCHEMA_SQL)
    except Exception as exc:
        raise _db_error("initializing the credentials table", exc) from exc
    _schema_ready = True
    logger.info("Credential database ready (table %s)", TABLE)


def close() -> None:
    """Close the pool (shutdown, tests). The next call reopens it."""
    global _pool, _pool_url, _schema_ready
    with _lock:
        if _pool is not None:
            _pool.close()
        _pool, _pool_url, _schema_ready = None, None, False


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------
def _encrypt(info: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(info).encode()).decode()


def _decrypt(ciphertext: str) -> dict[str, Any]:
    try:
        data = json.loads(_fernet().decrypt(ciphertext.encode()))
    except InvalidToken as exc:
        raise TokenStoreError(
            "The stored Google credentials could not be decrypted with "
            "GOOGLE_TOKEN_ENCRYPTION_KEY (the key changed?). Restore the "
            "previous key (comma-separate old and new keys to rotate) or "
            "authorize again via GET /auth/google/authorize."
        ) from exc
    except ValueError as exc:
        raise TokenStoreError("The stored Google credentials are corrupted.") from exc
    if not isinstance(data, dict):
        raise TokenStoreError("The stored Google credentials are corrupted.")
    return data


def _columns(info: dict[str, Any]) -> tuple[str, str | None, str]:
    """(ciphertext, expiry, scopes) — expiry/scopes are non-secret metadata."""
    scopes = info.get("scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    expiry = info.get("expiry")
    if isinstance(expiry, str) and expiry and not expiry.endswith(("Z", "+00:00")):
        expiry += "Z"  # google-auth writes naive UTC timestamps
    return _encrypt(info), expiry or None, " ".join(scopes)


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------
def load() -> StoredCredentials | None:
    """The stored credentials, or None when OAuth has not been completed."""
    row = _run(
        "loading Google credentials",
        f"SELECT encrypted_credentials, version, updated_at FROM {TABLE} WHERE id = %s",
        (CREDENTIALS_ID,),
    )
    if row is None:
        return None
    return StoredCredentials(info=_decrypt(row[0]), version=row[1], updated_at=row[2])


def metadata() -> StoredMetadata | None:
    """Non-secret row metadata, without decrypting anything."""
    row = _run(
        "reading credential metadata",
        f"SELECT version, token_expiry, scopes, created_at, updated_at FROM {TABLE} "
        "WHERE id = %s",
        (CREDENTIALS_ID,),
    )
    return StoredMetadata(*row) if row else None


def save(info: dict[str, Any]) -> int:
    """Store a NEW authorization, replacing any stored one. Returns the version.

    Used by the OAuth callback (and an explicit ``--replace`` import): a
    fresh user consent supersedes whatever was stored before.
    """
    ciphertext, expiry, scopes = _columns(info)
    row = _run(
        "saving Google credentials",
        f"""
        INSERT INTO {TABLE} AS c (id, encrypted_credentials, version, token_expiry, scopes)
        VALUES (%s, %s, 1, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            encrypted_credentials = EXCLUDED.encrypted_credentials,
            version = c.version + 1,
            token_expiry = EXCLUDED.token_expiry,
            scopes = EXCLUDED.scopes,
            updated_at = now()
        RETURNING version
        """,
        (CREDENTIALS_ID, ciphertext, expiry, scopes),
    )
    return row[0]


def save_if_version(info: dict[str, Any], expected_version: int) -> int | None:
    """Store refreshed credentials only if nobody wrote since ``expected_version``.

    Returns the new version, or None when the row changed (or was deleted)
    meanwhile — the caller must then reload instead of overwriting.
    """
    ciphertext, expiry, scopes = _columns(info)
    row = _run(
        "saving refreshed Google credentials",
        f"""
        UPDATE {TABLE} SET
            encrypted_credentials = %s,
            version = version + 1,
            token_expiry = %s,
            scopes = %s,
            updated_at = now()
        WHERE id = %s AND version = %s
        RETURNING version
        """,
        (ciphertext, expiry, scopes, CREDENTIALS_ID, expected_version),
    )
    return row[0] if row else None


def save_if_absent(info: dict[str, Any]) -> int | None:
    """Store credentials only when none are stored (safe one-time import)."""
    ciphertext, expiry, scopes = _columns(info)
    row = _run(
        "importing Google credentials",
        f"""
        INSERT INTO {TABLE} (id, encrypted_credentials, version, token_expiry, scopes)
        VALUES (%s, %s, 1, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING version
        """,
        (CREDENTIALS_ID, ciphertext, expiry, scopes),
    )
    return row[0] if row else None


def delete() -> bool:
    """Remove the stored credentials (explicit logout). True if a row existed."""
    row = _run(
        "deleting Google credentials",
        f"DELETE FROM {TABLE} WHERE id = %s RETURNING id",
        (CREDENTIALS_ID,),
    )
    return row is not None

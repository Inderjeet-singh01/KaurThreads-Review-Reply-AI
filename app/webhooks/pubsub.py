"""Google Cloud Pub/Sub push: request authentication and message decoding.

Authentication follows Google's "authenticated push" scheme: Pub/Sub signs
an OIDC ID token (JWT) as the subscription's push service account and sends
it as ``Authorization: Bearer <JWT>``. The token is verified for

* a valid Google signature (Google's public certificates),
* a Google issuer (``accounts.google.com`` / ``https://accounts.google.com``),
* an unexpired ``exp`` / sane ``iat``,
* the expected audience (``PUBSUB_PUSH_AUDIENCE``),
* the expected service account (``email`` == ``PUBSUB_PUSH_SERVICE_ACCOUNT``,
  with ``email_verified``).

The token itself is never logged or returned.

Business Profile notification payload: Google's documentation names the
fields only informally (``reviewName`` / ``locationName`` alongside the
notification type), so the decoder accepts the known spellings and always
re-validates the review resource name before anything uses it. The event
is only a trigger — the review itself is re-fetched from Google.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token

from app.config import settings

logger = logging.getLogger(__name__)

# Small tolerance for clock differences between Google and this server.
_CLOCK_SKEW_SECONDS = 10

_REVIEW_NAME_RE = re.compile(
    r"^accounts/(?P<account>[^/]+)/locations/(?P<location>[^/]+)/reviews/(?P<review>[^/]+)$"
)
_LOCATION_NAME_RE = re.compile(r"^(?:accounts/[^/]+/)?locations/(?P<location>[^/]+)$")

# Field spellings seen for Business Profile notifications.
_TYPE_KEYS = ("type", "notificationType")
_REVIEW_KEYS = ("review", "reviewName")
_LOCATION_KEYS = ("location", "locationName")


class PubSubAuthError(Exception):
    """The push request is not an authenticated Pub/Sub delivery."""


class PubSubMissingTokenError(PubSubAuthError):
    """No ``Authorization: Bearer`` token was sent."""


class PubSubAuthNotConfiguredError(PubSubAuthError):
    """PUBSUB_PUSH_AUDIENCE / PUBSUB_PUSH_SERVICE_ACCOUNT are not set."""


class MalformedPushError(Exception):
    """The push body or the notification inside it cannot be used."""


def pubsub_auth_configured() -> bool:
    return bool(settings.pubsub_push_audience and settings.pubsub_push_service_account)


def verify_push_token(authorization: str | None) -> dict[str, Any]:
    """Verify the Pub/Sub push JWT and return its claims.

    Raises :class:`PubSubAuthNotConfiguredError` when verification settings
    are missing (every request is rejected), or :class:`PubSubAuthError`
    for a missing, malformed, forged, expired or foreign token.
    """
    if not pubsub_auth_configured():
        raise PubSubAuthNotConfiguredError(
            "PUBSUB_PUSH_AUDIENCE and PUBSUB_PUSH_SERVICE_ACCOUNT must be set"
        )
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise PubSubMissingTokenError("missing bearer token")

    try:
        # Checks signature, exp/iat, audience and the Google issuer.
        claims = id_token.verify_oauth2_token(
            token,
            google_auth_requests.Request(),
            audience=settings.pubsub_push_audience,
            clock_skew_in_seconds=_CLOCK_SKEW_SECONDS,
        )
    except Exception as exc:  # invalid token, wrong audience, expired, cert fetch...
        # Only the exception type is reported: messages can echo token parts.
        raise PubSubAuthError(f"token verification failed ({type(exc).__name__})") from None

    if claims.get("email") != settings.pubsub_push_service_account:
        raise PubSubAuthError("token was not issued for the expected service account")
    if claims.get("email_verified") is not True:
        raise PubSubAuthError("token email is not verified")
    return claims


@dataclass(frozen=True)
class ReviewNotification:
    """A decoded Business Profile notification from a Pub/Sub push."""

    event_type: str
    message_id: str | None
    delivery_attempt: int | None
    review_resource_name: str | None = None
    review_id: str | None = None
    location_id: str | None = None


def _first_string(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_push_body(body: Any) -> ReviewNotification:
    """Decode a wrapped Pub/Sub push body into a :class:`ReviewNotification`.

    Raises :class:`MalformedPushError` when the envelope, the base64 data,
    the JSON payload, or (for NEW_REVIEW) the review resource name is
    invalid. Non-review notification types decode fine and are ignored by
    the caller.
    """
    if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
        raise MalformedPushError("push body has no 'message' object")
    message = body["message"]
    message_id = message.get("messageId") or message.get("message_id")
    delivery_attempt = body.get("deliveryAttempt")
    if not isinstance(delivery_attempt, int):
        delivery_attempt = None

    raw_data = message.get("data")
    if not isinstance(raw_data, str) or not raw_data:
        raise MalformedPushError("message has no data")
    try:
        data = json.loads(base64.b64decode(raw_data, validate=True))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise MalformedPushError("message data is not base64-encoded JSON") from exc
    if not isinstance(data, dict):
        raise MalformedPushError("message data is not a JSON object")

    attributes = message.get("attributes") if isinstance(message.get("attributes"), dict) else {}
    event_type = (_first_string(data, _TYPE_KEYS) or _first_string(attributes, _TYPE_KEYS)).upper()
    if not event_type:
        raise MalformedPushError(
            f"notification has no type (payload keys: {sorted(data)})"
        )

    review_name = _first_string(data, _REVIEW_KEYS)
    match = _REVIEW_NAME_RE.match(review_name) if review_name else None
    if event_type != "NEW_REVIEW":
        return ReviewNotification(
            event_type=event_type,
            message_id=message_id,
            delivery_attempt=delivery_attempt,
            review_resource_name=review_name or None,
            review_id=match["review"] if match else None,
            location_id=match["location"] if match else None,
        )

    if match is None:
        raise MalformedPushError(
            "NEW_REVIEW notification has no valid review resource name "
            f"(payload keys: {sorted(data)})"
        )
    location_name = _first_string(data, _LOCATION_KEYS)
    if location_name:
        location_match = _LOCATION_NAME_RE.match(location_name)
        if location_match is None or location_match["location"] != match["location"]:
            raise MalformedPushError("location does not match the review resource name")

    return ReviewNotification(
        event_type=event_type,
        message_id=message_id,
        delivery_attempt=delivery_attempt,
        review_resource_name=review_name,
        review_id=match["review"],
        location_id=match["location"],
    )

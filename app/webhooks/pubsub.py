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
fields only informally (``type`` / ``review`` / ``location``, also seen as
``notificationType`` / ``reviewName`` / ``locationName``), and the review
reference has been seen in more than one shape. The decoder therefore
accepts the known spellings, normalizes the values
(:mod:`app.google.resource_names`) and validates every id segment. A
NEW_REVIEW with a valid location but an unreadable review reference is NOT
rejected: the caller resolves the review from Google instead (see
:mod:`app.automation.resolver`). The event is only a trigger — the review
itself is always re-fetched from Google.
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
from app.google.resource_names import (
    normalize_reference,
    parse_location_reference,
    parse_review_reference,
    review_resource_name,
    sanitize_for_log,
)

logger = logging.getLogger(__name__)

# Small tolerance for clock differences between Google and this server.
_CLOCK_SKEW_SECONDS = 10

# Field spellings accepted for Business Profile notifications (matched
# case-insensitively).
_TYPE_KEYS = ("type", "notificationType", "notification_type")
_REVIEW_KEYS = ("review", "reviewName", "review_name")
_LOCATION_KEYS = ("location", "locationName", "location_name")
_URLSAFE_B64_RE = re.compile(r"^[A-Za-z0-9_-]*={0,2}$")


class PubSubAuthError(Exception):
    """The push request is not an authenticated Pub/Sub delivery."""


class PubSubMissingTokenError(PubSubAuthError):
    """No ``Authorization: Bearer`` token was sent."""


class PubSubAuthNotConfiguredError(PubSubAuthError):
    """PUBSUB_PUSH_AUDIENCE / PUBSUB_PUSH_SERVICE_ACCOUNT are not set."""


class MalformedPushError(Exception):
    """The push body or the notification inside it cannot be used."""

    def __init__(self, message: str, message_id: str | None = None):
        super().__init__(message)
        self.message_id = message_id


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
    """A decoded Business Profile notification from a Pub/Sub push.

    For NEW_REVIEW, ``location_id`` is always set. ``review_id`` is set
    when the review reference could be parsed; otherwise ``review_issue``
    says why, and the caller resolves the review from Google (fallback).
    ``raw_review`` / ``raw_location`` are sanitized renderings for logs.
    """

    event_type: str
    message_id: str | None
    delivery_attempt: int | None
    review_resource_name: str | None = None
    review_id: str | None = None
    location_id: str | None = None
    account_id: str | None = None
    raw_review: str = "-"
    raw_location: str = "-"
    review_issue: str | None = None
    publish_time: str | None = None
    payload_keys: tuple[str, ...] = ()


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """The first non-empty value among ``keys`` (case-insensitive key match)."""
    lowered = {key.lower(): value for key, value in data.items() if isinstance(key, str)}
    for key in keys:
        value = data.get(key, lowered.get(key.lower()))
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict) and value:
            return value
    return None


def _first_string(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = _first_value(data, keys)
    return value if isinstance(value, str) else ""


def _decode_data(raw_data: str) -> Any:
    """base64 (standard or URL-safe, padding optional) -> JSON value."""
    compact = "".join(raw_data.split())
    padded = compact + "=" * (-len(compact) % 4)
    try:
        if _URLSAFE_B64_RE.match(padded) and ("-" in padded or "_" in padded):
            decoded = base64.urlsafe_b64decode(padded)
        else:
            decoded = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MalformedPushError("message data is not base64-encoded JSON") from exc
    try:
        value = json.loads(decoded)
        if isinstance(value, str):  # JSON-encoded JSON string
            value = json.loads(value)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedPushError("message data is not base64-encoded JSON") from exc
    return value


def _unwrap(body: Any) -> tuple[dict[str, Any], dict[str, Any], str | None, int | None, str | None]:
    """Return (data, attributes, message_id, delivery_attempt, publish_time)."""
    if not isinstance(body, dict):
        raise MalformedPushError("push body is not a JSON object")
    delivery_attempt = body.get("deliveryAttempt")
    if not isinstance(delivery_attempt, int) or isinstance(delivery_attempt, bool):
        delivery_attempt = None

    message = body.get("message")
    if not isinstance(message, dict):
        # Subscriptions with "payload unwrapping" (--push-no-wrapper) POST the
        # notification itself; accept it only when it looks like one.
        if _first_string(body, _TYPE_KEYS):
            return body, {}, None, delivery_attempt, None
        raise MalformedPushError("push body has no 'message' object")

    message_id = message.get("messageId") or message.get("message_id")
    message_id = str(message_id) if message_id else None
    publish_time = message.get("publishTime") or message.get("publish_time")
    publish_time = publish_time if isinstance(publish_time, str) else None
    attributes = message.get("attributes") if isinstance(message.get("attributes"), dict) else {}

    raw_data = message.get("data")
    if not isinstance(raw_data, str) or not raw_data.strip():
        raise MalformedPushError("message has no data", message_id=message_id)
    try:
        data = _decode_data(raw_data)
    except MalformedPushError as exc:
        raise MalformedPushError(str(exc), message_id=message_id) from exc
    if not isinstance(data, dict):
        raise MalformedPushError("message data is not a JSON object", message_id=message_id)
    return data, attributes, message_id, delivery_attempt, publish_time


def parse_push_body(body: Any) -> ReviewNotification:
    """Decode a Pub/Sub push body into a :class:`ReviewNotification`.

    Raises :class:`MalformedPushError` only when redelivery can never help:
    an unusable envelope / base64 / JSON payload, no notification type, or
    a NEW_REVIEW without any valid location (neither in ``location`` nor in
    the review resource name), or whose location and review disagree.

    A NEW_REVIEW whose review reference cannot be parsed, but whose
    location is valid, decodes fine with ``review_id=None`` and a
    ``review_issue``: the caller then resolves the review from Google.
    """
    data, attributes, message_id, delivery_attempt, publish_time = _unwrap(body)

    event_type = (_first_string(data, _TYPE_KEYS) or _first_string(attributes, _TYPE_KEYS))
    event_type = event_type.strip().upper()
    if not event_type:
        raise MalformedPushError(
            f"notification has no type (payload keys: {sorted(map(str, data))})",
            message_id=message_id,
        )

    raw_review = _first_value(data, _REVIEW_KEYS)
    raw_location = _first_value(data, _LOCATION_KEYS)
    review_ref = parse_review_reference(raw_review) if raw_review is not None else None
    location_ref = parse_location_reference(raw_location) if raw_location is not None else None
    common = {
        "event_type": event_type,
        "message_id": message_id,
        "delivery_attempt": delivery_attempt,
        "raw_review": sanitize_for_log(raw_review),
        "raw_location": sanitize_for_log(raw_location),
        "publish_time": publish_time,
        "payload_keys": tuple(sorted(sanitize_for_log(key, 40) for key in data)),
    }

    if event_type != "NEW_REVIEW":
        # Ignored by the caller; best-effort ids for the log line only.
        return ReviewNotification(
            **common,
            review_resource_name=normalize_reference(raw_review) or None,
            review_id=review_ref.review_id if review_ref else None,
            location_id=(review_ref.location_id if review_ref else None)
            or (location_ref.location_id if location_ref else None),
        )

    review_location = review_ref.location_id if review_ref else None
    review_account = review_ref.account_id if review_ref else None
    if location_ref and review_location and location_ref.location_id != review_location:
        raise MalformedPushError(
            "location does not match the review resource name "
            f"(location_raw={common['raw_location']}, review_raw={common['raw_review']})",
            message_id=message_id,
        )
    if location_ref and review_account and location_ref.account_id \
            and location_ref.account_id != review_account:
        raise MalformedPushError(
            "account does not match the review resource name "
            f"(location_raw={common['raw_location']}, review_raw={common['raw_review']})",
            message_id=message_id,
        )
    location_id = review_location or (location_ref.location_id if location_ref else None)
    account_id = review_account or (location_ref.account_id if location_ref else None)
    if not location_id:
        raise MalformedPushError(
            "NEW_REVIEW notification has no valid location "
            f"(location_raw={common['raw_location']}, review_raw={common['raw_review']}, "
            f"payload keys: {sorted(map(str, data))})",
            message_id=message_id,
        )

    if review_ref is None:
        issue = (
            "review field missing" if raw_review is None
            else "review reference could not be parsed"
        )
        return ReviewNotification(
            **common, location_id=location_id, account_id=account_id, review_issue=issue,
        )
    return ReviewNotification(
        **common,
        review_resource_name=review_resource_name(review_ref.review_id, location_id, account_id),
        review_id=review_ref.review_id,
        location_id=location_id,
        account_id=account_id,
    )

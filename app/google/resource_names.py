"""Tolerant parsing of Business Profile review / location references.

Notifications (Pub/Sub) and API responses do not always spell resource
names the same way. This module turns whatever arrived into plain ids,
without ever trusting the value blindly:

* values are trimmed, URL-decoded (at most twice), stripped of a URL
  scheme/host/API version prefix and of harmless leading/trailing slashes;
* every path segment must be a plain id (letters, digits, ``-_.~=+``; no
  empty segments, no ``.``/``..``, no leftover ``%``), so path traversal
  and junk never reach a Google URL;
* only the documented shapes are accepted.

Review references::

    accounts/{account}/locations/{location}/reviews/{review}
    locations/{location}/reviews/{review}
    reviews/{review}
    {review}                       (bare id; the caller must know the location)

Location references::

    accounts/{account}/locations/{location}
    locations/{location}
    {location}                     (bare id)

Parsing never proves that a review exists: Google is the source of truth
and the review is always fetched before anything is done with it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-.~=+]{1,512}$")
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
# "/v4/", "/v1/", "/v1beta1/" ... in front of the resource path of an API URL.
_API_VERSION_RE = re.compile(r"^v\d+(?:(?:alpha|beta)\d*)?/")
_COLLECTIONS = {"accounts", "locations", "reviews"}
_MAX_RAW_LENGTH = 2048
_MAX_LOG_LENGTH = 200

# Keys under which a review / location object carries its resource name.
_REVIEW_OBJECT_KEYS = ("name", "reviewName", "review_name", "reviewId", "review_id")
_LOCATION_OBJECT_KEYS = ("name", "locationName", "location_name", "locationId", "location_id")


@dataclass(frozen=True)
class ReviewRef:
    review_id: str
    location_id: str | None = None
    account_id: str | None = None


@dataclass(frozen=True)
class LocationRef:
    location_id: str
    account_id: str | None = None


def sanitize_for_log(value: Any, limit: int = _MAX_LOG_LENGTH) -> str:
    """A short, single-line, escaped rendering of an untrusted value."""
    if value is None:
        return "-"
    if not isinstance(value, str):
        try:
            value = json.dumps(value, default=str, sort_keys=True)
        except (TypeError, ValueError):
            value = repr(value)
    # json.dumps escapes newlines/control characters (no log injection).
    text = json.dumps(value, ensure_ascii=True)[1:-1]
    return text if len(text) <= limit else text[:limit] + "…"


def _object_value(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        return ""
    return value


def normalize_reference(value: Any) -> str:
    """Normalize a raw resource reference to ``a/b/c`` form (``""`` if unusable)."""
    if not isinstance(value, str):
        return ""
    text = value.strip().strip("\"'").strip()
    if not text or len(text) > _MAX_RAW_LENGTH:
        return ""
    for _ in range(2):  # "accounts%2F1%2F..." and a double-encoded variant
        if "%" not in text:
            break
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded.strip()
    if _URL_RE.match(text):
        text = urlsplit(text).path
    text = text.strip().strip("/")
    text = _API_VERSION_RE.sub("", text, count=1)
    return text


def _segments(text: str) -> list[str] | None:
    parts = text.split("/") if text else []
    if not parts:
        return None
    for part in parts:
        if not _SEGMENT_RE.match(part) or not part.strip("."):
            return None
    return parts


def parse_review_reference(value: Any) -> ReviewRef | None:
    """Parse a review reference (string or review-like object), else ``None``."""
    parts = _segments(normalize_reference(_object_value(value, _REVIEW_OBJECT_KEYS)))
    if parts is None:
        return None
    if len(parts) == 6 and (parts[0], parts[2], parts[4]) == ("accounts", "locations", "reviews"):
        return ReviewRef(review_id=parts[5], location_id=parts[3], account_id=parts[1])
    if len(parts) == 4 and (parts[0], parts[2]) == ("locations", "reviews"):
        return ReviewRef(review_id=parts[3], location_id=parts[1])
    if len(parts) == 2 and parts[0] == "reviews":
        return ReviewRef(review_id=parts[1])
    if len(parts) == 1 and parts[0] not in _COLLECTIONS:
        return ReviewRef(review_id=parts[0])
    return None


def parse_location_reference(value: Any) -> LocationRef | None:
    """Parse a location reference (string or location-like object), else ``None``."""
    parts = _segments(normalize_reference(_object_value(value, _LOCATION_OBJECT_KEYS)))
    if parts is None:
        return None
    if len(parts) == 4 and (parts[0], parts[2]) == ("accounts", "locations"):
        return LocationRef(location_id=parts[3], account_id=parts[1])
    if len(parts) == 2 and parts[0] == "locations":
        return LocationRef(location_id=parts[1])
    if len(parts) == 1 and parts[0] not in _COLLECTIONS:
        return LocationRef(location_id=parts[0])
    return None


def review_resource_name(review_id: str, location_id: str, account_id: str | None) -> str:
    """Best-effort full resource name, for logs (Google calls rebuild their own)."""
    location = f"locations/{location_id}/reviews/{review_id}"
    return f"accounts/{account_id}/{location}" if account_id else location

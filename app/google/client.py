"""Authenticated Google API client for the Business Profile APIs.

This module answers one question: "How do I create an authenticated Google
client?" It loads the OAuth token from ``credentials/google_token.json``,
keeps it fresh (refresh on expiry / 401), and exposes the Business Profile
REST endpoints this application needs.

Review business logic (what counts as unanswered, final checks, publishing
policy) lives in :mod:`app.google.reviews`.

Endpoints used (all documented by Google, all authorized with the
``business.manage`` scope):

* Reviews API (mybusiness v4)
    GET https://mybusiness.googleapis.com/v4/{parent=accounts/*/locations/*}/reviews
    GET https://mybusiness.googleapis.com/v4/{name=accounts/*/locations/*/reviews/*}
    PUT https://mybusiness.googleapis.com/v4/{name=accounts/*/locations/*/reviews/*}/reply
* My Business Account Management API (v1)
    GET https://mybusinessaccountmanagement.googleapis.com/v1/accounts
* My Business Business Information API (v1)
    GET https://mybusinessbusinessinformation.googleapis.com/v1/{parent=accounts/*}/locations
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from google.oauth2.credentials import Credentials

from app.auth.google_oauth import (
    GoogleOAuthError,
    force_refresh_credentials,
    load_credentials,
    refresh_credentials_if_needed,
)

logger = logging.getLogger(__name__)

REVIEW_BASE_URL = "https://mybusiness.googleapis.com/v4"
ACCOUNT_MANAGEMENT_BASE_URL = (
    "https://mybusinessaccountmanagement.googleapis.com/v1"
)
BUSINESS_INFORMATION_BASE_URL = (
    "https://mybusinessbusinessinformation.googleapis.com/v1"
)

REQUEST_TIMEOUT_SECONDS = 30


class GoogleAPIError(Exception):
    """A call to a Google API endpoint failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def get_google_client() -> "GoogleBusinessClient":
    """Create an authenticated Business Profile client from the token file.

    Raises GoogleOAuthError when the OAuth flow has not been completed or the
    stored credentials are unusable.
    """
    return GoogleBusinessClient(load_credentials())


class GoogleBusinessClient:
    """Thin authenticated client for the Business Profile REST APIs."""

    def __init__(self, credentials: Credentials):
        self._credentials = credentials

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        """Return request headers with a currently-valid access token."""
        refresh_credentials_if_needed(self._credentials)
        token = self._credentials.token
        if not token:
            raise GoogleOAuthError(
                "No valid Google access token available. Re-run the OAuth "
                "flow via GET /auth/google/authorize."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        _retried: bool = False,
    ) -> dict[str, Any]:
        """Perform an authenticated request and return the JSON payload.

        A 401 is treated as "token expired": the token is refreshed once and
        the request retried. Any other error status raises GoogleAPIError
        with a safe message (no tokens or credential data are included).
        """
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.error("Google API request failed (%s %s): %s", method, url, exc)
            raise GoogleAPIError(f"Could not reach the Google API: {exc}") from exc

        if response.status_code == 401 and not _retried:
            logger.warning("Google returned 401; refreshing token and retrying once")
            if force_refresh_credentials(self._credentials):
                return self._request(
                    method, url, json_body=json_body, params=params, _retried=True
                )

        if response.status_code >= 400:
            raise GoogleAPIError(
                self._error_message(response), status=response.status_code
            )

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            logger.warning("Google API returned a non-JSON payload (%s %s)", method, url)
            return {}

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        """Build a safe, actionable error message for a failed response."""
        detail = ""
        try:
            payload = response.json()
            detail = str((payload.get("error") or {}).get("message", ""))
        except ValueError:
            detail = ""
        status = response.status_code
        if status == 401:
            base = (
                "Google rejected the access token (authentication failed). "
                "Re-run the OAuth flow."
            )
        elif status == 403:
            base = (
                "Google denied access (permission denied). Make sure the "
                "Google Business Profile APIs are enabled for your project, "
                "your app has been granted access, and the signed-in Google "
                "account can manage this Business Profile."
            )
        elif status == 404:
            base = "Google could not find the requested resource."
        elif status == 429:
            # Distinguish quota-0 (API access not approved) from transient
            # rate-limiting.  Google's error message for a zero-quota project
            # typically contains "Quota exceeded for quota metric" with
            # "per minute" language on a consumer/project basis.
            if detail and "Quota exceeded" in detail:
                base = (
                    "Google Business Profile API access is not approved for "
                    "this project yet. Your project's quota is 0. Please "
                    "request GBP API access from Google (see "
                    "https://developers.google.com/my-business/content/prereqs) "
                    "before using the application."
                )
            else:
                base = (
                    "Google API rate limit exceeded. Please wait and retry."
                )
        else:
            base = f"Google API request failed with HTTP {status}"
        if detail:
            return f"{base} Details: {detail[:300]}"
        return base

    # ------------------------------------------------------------------
    # Business Profile REST endpoints
    # ------------------------------------------------------------------
    def list_accounts(self, page_token: str | None = None) -> dict[str, Any]:
        """List the My Business accounts the user owns/manages (v1).

        Returns the raw API response: ``accounts`` (each with a ``name`` of
        the form ``accounts/{account_id}``) and ``nextPageToken`` when
        present.
        """
        params = {"pageToken": page_token} if page_token else None
        return self._request("GET", f"{ACCOUNT_MANAGEMENT_BASE_URL}/accounts", params=params)

    def list_locations(
        self, account_name: str, page_token: str | None = None
    ) -> dict[str, Any]:
        """List the locations of a My Business account (business info v1).

        ``account_name`` has the form ``accounts/{account_id}``. ``readMask``
        is a required query parameter of this endpoint; only ``name`` and
        ``title`` are needed here.
        """
        params: dict[str, Any] = {"readMask": "name,title"}
        if page_token:
            params["pageToken"] = page_token
        return self._request(
            "GET",
            f"{BUSINESS_INFORMATION_BASE_URL}/{account_name}/locations",
            params=params,
        )

    def list_reviews(
        self,
        location_name: str,
        page_size: int,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List reviews of a location (Reviews API, mybusiness v4).

        ``location_name`` has the form
        ``accounts/{account_id}/locations/{location_id}``. Returns the raw
        API response: ``reviews``, plus ``nextPageToken`` when there are
        more pages (``pageSize`` max is 50).
        """
        params: dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        return self._request(
            "GET", f"{REVIEW_BASE_URL}/{location_name}/reviews", params=params
        )

    def get_review(self, review_name: str) -> dict[str, Any]:
        """Fetch one review (Reviews API, mybusiness v4).

        ``review_name`` has the form
        ``accounts/{account_id}/locations/{location_id}/reviews/{review_id}``.
        Returns the raw ``Review`` object; Google answers 404 when the review
        does not exist or was deleted.
        """
        return self._request("GET", f"{REVIEW_BASE_URL}/{review_name}")

    def update_review_reply(
        self, review_name: str, comment: str
    ) -> dict[str, Any]:
        """Publish the business reply on a review (Reviews API, mybusiness v4).

        PUTs a ``ReviewReply`` object (``{"comment": <reply text>}``) to the
        review's ``reply`` sub-resource, creating the reply (or replacing an
        existing one). Returns the ``ReviewReply`` object Google stored.
        """
        return self._request(
            "PUT",
            f"{REVIEW_BASE_URL}/{review_name}/reply",
            json_body={"comment": comment},
        )

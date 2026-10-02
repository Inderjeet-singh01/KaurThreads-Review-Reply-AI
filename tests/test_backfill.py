"""Tests for the bulk backfill (reply to all pending reviews).

The real processor pipeline runs for every review; only Google and the AI
providers are mocked (same harness as tests/test_automation.py).

    python -m unittest tests.test_backfill
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest import mock

import httpx
from fastapi import FastAPI

from app.ai.gemini_client import GeminiError
from app.ai.groq_client import GroqError
from app.api import automation as automation_api
from app.automation import backfill
from app.automation.backfill import BackfillJobStatus, BackfillRejected
from app.automation.processor import MAX_PROCESSING_ATTEMPTS
from app.google import reviews as google_reviews
from app.google.client import GoogleAPIError
from tests.test_automation import (
    FAIL,
    GOOD_REPLY,
    LOCATION_ID,
    PASS,
    RAW_REVIEW,
    AutomationTestCase,
)

# The real helper, captured before BackfillTestCase patches it.
_REAL_LIST_PENDING = backfill._list_pending


def _review(review_id: str, replied: bool = False) -> dict:
    raw = {**RAW_REVIEW, "reviewId": review_id}
    if replied:
        raw["reviewReply"] = {"comment": "Thank you!"}
    return raw


class BackfillTestCase(AutomationTestCase):
    """Automation enabled, no delay, Google listing mocked per test."""

    def setUp(self):
        super().setUp()
        backfill.reset_state()
        self.addCleanup(backfill.reset_state)
        self._patch_settings(
            automation_backfill_delay_seconds=0,
            automation_backfill_retry_delay_seconds=0,
        )
        # Google state per review id; get_review always reads the latest.
        self.google: dict[str, dict] = {}
        self.get_review.side_effect = self._get_review
        self.list_pending = self._patch(
            "app.automation.backfill._list_pending", side_effect=self._list_pending
        )

    def _get_review(self, review_id, location_id=None):
        value = self.google[review_id]
        if isinstance(value, Exception):
            raise value
        return value

    def _list_pending(self, location_id):
        return LOCATION_ID, [rid for rid, raw in self.google.items()
                             if isinstance(raw, Exception) or not raw.get("reviewReply")]

    def set_reviews(self, *review_ids: str) -> None:
        for review_id in review_ids:
            self.google[review_id] = _review(review_id)

    async def run_backfill(self, location_id: str | None = LOCATION_ID) -> dict:
        job = await backfill.start_backfill(location_id)
        job = await backfill.wait_for_job(job.job_id, timeout=10)
        self.assertFalse(job.active, "backfill did not finish")
        return job.summary()

    def items(self, summary: dict) -> dict[str, dict]:
        return {item["review_id"]: item for item in summary["items"]}


# --- Core flow ----------------------------------------------------------------------
class BackfillFlowTests(BackfillTestCase):
    # 1 / 5 / 11
    async def test_every_unanswered_review_is_published(self):
        self.set_reviews("a", "b", "c")
        summary = await self.run_backfill()
        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(
            {k: summary[k] for k in ("total", "processed", "published", "skipped", "failed")},
            {"total": 3, "processed": 3, "published": 3, "skipped": 0, "failed": 0},
        )
        self.assertEqual(summary["location_id"], LOCATION_ID)
        self.assertIsNotNone(summary["finished_at"])
        self.assertIsNone(summary["current_review_id"])
        for item in summary["items"]:
            self.assertEqual(item["outcome"], "PUBLISHED")
            self.assertEqual(item["final_status"], "PUBLISHED")
            self.assertEqual(item["generation_provider"], "groq")
            self.assertEqual(item["validation_results"], ["PASS"])
            self.assertEqual(item["publish_result"], "published")
        self.assertEqual(
            [c.args for c in self.publish.call_args_list],
            [("a", GOOD_REPLY), ("b", GOOD_REPLY), ("c", GOOD_REPLY)],
        )

    # 3
    async def test_reviews_are_processed_sequentially_in_order(self):
        self.set_reviews("a", "b", "c", "d")
        in_flight, peak, order = 0, 0, []
        lock = threading.Lock()

        def slow_get_review(review_id, location_id=None):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
                order.append(review_id)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return self.google[review_id]

        self.get_review.side_effect = slow_get_review
        await self.run_backfill()
        self.assertEqual(peak, 1)
        # Each review: initial fetch, final check and post-publish verification,
        # before the next review starts.
        self.assertEqual(order, [rid for rid in "abcd" for _ in range(3)])

    # 4
    async def test_review_replied_since_listing_is_skipped(self):
        self.set_reviews("a", "b")
        job = await backfill.start_backfill(LOCATION_ID)
        self.google["a"] = _review("a", replied=True)  # answered meanwhile
        await backfill.wait_for_job(job.job_id, timeout=10)
        item = self.items(job.summary())["a"]
        self.assertEqual(item["outcome"], "SKIPPED")
        self.assertEqual(item["final_status"], "SKIPPED_ALREADY_REPLIED")
        self.assertEqual([c.args[0] for c in self.publish.call_args_list], ["b"])
        self.assertEqual(job.summary()["skipped"], 1)

    async def test_reply_added_during_generation_blocks_publish(self):
        self.set_reviews("a")
        calls = {"n": 0}

        def get_review(review_id, location_id=None):
            calls["n"] += 1
            return _review(review_id, replied=calls["n"] > 1)  # replied before final check

        self.get_review.side_effect = get_review
        summary = await self.run_backfill()
        self.assertEqual(self.items(summary)["a"]["final_status"], "SKIPPED_ALREADY_REPLIED")
        self.publish.assert_not_called()

    # 6
    async def test_dry_run_never_publishes(self):
        self._patch_settings(auto_reply_dry_run=True)
        self.set_reviews("a", "b")
        with self.assertLogs("app.automation.processor", level="INFO") as logs:
            summary = await self.run_backfill()
        self.publish.assert_not_called()
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["would_publish"], 2)
        self.assertEqual(summary["published"], 0)
        self.assertEqual({i["outcome"] for i in summary["items"]}, {"DRY_RUN"})
        self.assertEqual(sum("WOULD_PUBLISH" in line for line in logs.output), 2)

    # 7 / 14
    async def test_google_failure_on_one_review_does_not_stop_the_batch(self):
        self.set_reviews("a", "b", "c")
        self.google["b"] = GoogleAPIError("Google API request failed with HTTP 500", status=500)
        summary = await self.run_backfill()
        items = self.items(summary)
        self.assertEqual(items["b"]["outcome"], "FAILED")
        self.assertEqual(items["b"]["error_stage"], "fetch")
        self.assertIn("HTTP 500", items["b"]["error"])
        self.assertEqual(items["a"]["outcome"], "PUBLISHED")
        self.assertEqual(items["c"]["outcome"], "PUBLISHED")
        self.assertEqual((summary["published"], summary["failed"]), (2, 1))
        self.assertEqual(summary["status"], "COMPLETED")

    async def test_unexpected_crash_on_one_review_does_not_stop_the_batch(self):
        self.set_reviews("a", "b")
        real = backfill._process_item

        async def flaky(job, item):
            if item.review_id == "a":
                raise RuntimeError("boom")
            await real(job, item)

        with mock.patch.object(backfill, "_process_item", side_effect=flaky), \
                self.assertLogs("app.automation.backfill", level="ERROR"):
            summary = await self.run_backfill()
        items = self.items(summary)
        self.assertEqual(items["a"]["outcome"], "FAILED")
        self.assertEqual(items["b"]["outcome"], "PUBLISHED")

    async def test_deleted_review_is_skipped(self):
        self.set_reviews("a")
        self.google["a"] = google_reviews.ReviewNotFoundError("gone")
        summary = await self.run_backfill()
        self.assertEqual(self.items(summary)["a"]["final_status"], "SKIPPED_NOT_FOUND")
        self.assertEqual(summary["skipped"], 1)

    # 15
    async def test_ai_generation_failure(self):
        self.set_reviews("a", "b")
        self.groq.side_effect = GroqError("RateLimitError (HTTP 429)")
        self.gemini.side_effect = GeminiError("unavailable")
        summary = await self.run_backfill()
        item = self.items(summary)["a"]
        self.assertEqual(item["outcome"], "FAILED")
        self.assertEqual(item["final_status"], "FAILED_GENERATION")
        self.assertEqual(item["error_stage"], "generation")
        self.assertEqual(item["attempts"], MAX_PROCESSING_ATTEMPTS)
        self.assertEqual(summary["failed"], 2)
        self.publish.assert_not_called()

    # 16
    async def test_validation_failure_is_final_and_not_retried(self):
        self.set_reviews("a")
        self.validate.side_effect = [FAIL, FAIL]
        summary = await self.run_backfill()
        item = self.items(summary)["a"]
        self.assertEqual(item["outcome"], "FAILED")
        self.assertEqual(item["final_status"], "FAILED_VALIDATION")
        self.assertEqual(item["validation_results"], ["FAIL", "FAIL"])
        self.assertEqual(item["attempts"], 1)
        self.assertIn("validation", item["error"])
        self.assertEqual(self.groq.call_count, 2)  # one generation + ONE regeneration
        self.publish.assert_not_called()

    async def test_validation_fail_then_pass_publishes_regenerated_reply(self):
        self.set_reviews("a")
        self.validate.side_effect = [FAIL, PASS]
        summary = await self.run_backfill()
        self.assertEqual(self.items(summary)["a"]["validation_results"], ["FAIL", "PASS"])
        self.publish.assert_called_once()

    # 8
    async def test_retryable_failure_is_retried_then_succeeds(self):
        self.set_reviews("a")
        self.groq.side_effect = [GroqError("timeout"), GOOD_REPLY]
        self.gemini.side_effect = GeminiError("unavailable")
        summary = await self.run_backfill()
        item = self.items(summary)["a"]
        self.assertEqual(item["outcome"], "PUBLISHED")
        self.assertEqual(item["attempts"], 2)

    async def test_retries_are_bounded_by_max_processing_attempts(self):
        self.set_reviews("a")
        self.google["a"] = GoogleAPIError("Service unavailable", status=503)
        summary = await self.run_backfill()
        item = self.items(summary)["a"]
        self.assertEqual(item["attempts"], MAX_PROCESSING_ATTEMPTS)
        self.assertEqual(self.get_review.call_count, MAX_PROCESSING_ATTEMPTS)
        self.assertFalse(item["retryable"])  # budget exhausted
        self.assertIn("manual review", item["error"])

    async def test_non_retryable_google_error_is_not_retried(self):
        self.set_reviews("a")
        self.google["a"] = GoogleAPIError("Google denied access", status=403)
        summary = await self.run_backfill()
        self.assertEqual(self.items(summary)["a"]["attempts"], 1)
        self.assertEqual(self.get_review.call_count, 1)

    async def test_delay_between_reviews_not_after_last(self):
        self._patch_settings(automation_backfill_delay_seconds=1.5)
        self.set_reviews("a", "b", "c")
        with mock.patch.object(backfill, "_pause", new=mock.AsyncMock()) as pause:
            await self.run_backfill()
        self.assertEqual([c.args[1] for c in pause.await_args_list], [1.5, 1.5])

    # 13
    async def test_empty_pending_list(self):
        job = await backfill.start_backfill(LOCATION_ID)
        self.assertEqual(job.status, BackfillJobStatus.COMPLETED)
        self.assertEqual(job.summary()["total"], 0)
        self.get_review.assert_not_called()
        # The slot is free again.
        self.assertIsNone(backfill._active_job_id)


# --- Duplicate protection and gates --------------------------------------------------
class BackfillSafetyTests(BackfillTestCase):
    # 9
    async def test_second_backfill_is_rejected_while_one_runs(self):
        self.set_reviews("a", "b")
        first = await backfill.start_backfill(LOCATION_ID)
        with self.assertRaises(BackfillRejected) as ctx:
            await backfill.start_backfill(LOCATION_ID)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.job_id, first.job_id)
        self.assertIn("already running", ctx.exception.reason)
        await backfill.wait_for_job(first.job_id, timeout=10)
        self.assertEqual(self.publish.call_count, 2)  # each review published once

    async def test_concurrent_starts_cannot_both_pass(self):
        self.set_reviews("a")
        results = await asyncio.gather(
            backfill.start_backfill(LOCATION_ID),
            backfill.start_backfill(LOCATION_ID),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, BackfillRejected) for r in results), 1)
        self.assertEqual(self.list_pending.call_count, 1)
        job = next(r for r in results if not isinstance(r, Exception))
        await backfill.wait_for_job(job.job_id, timeout=10)
        self.publish.assert_called_once()

    async def test_rerun_retries_reviews_that_failed_validation(self):
        # Reported bug: reviews that failed in one "reply to all" were skipped
        # as DUPLICATE ("recently processed") on every later click.
        self.set_reviews("a")
        self.validate.side_effect = [FAIL, FAIL, PASS]
        first = await self.run_backfill()
        self.assertEqual(self.items(first)["a"]["outcome"], "FAILED")
        second = await self.run_backfill()
        item = self.items(second)["a"]
        self.assertEqual(item["outcome"], "PUBLISHED")
        self.assertEqual(second["skipped"], 0)
        self.publish.assert_called_once()

    async def test_rerun_retries_reviews_whose_retry_budget_was_exhausted(self):
        self.set_reviews("a")
        self.google["a"] = GoogleAPIError("Service unavailable", status=503)
        first = await self.run_backfill()
        self.assertEqual(self.items(first)["a"]["outcome"], "FAILED")
        self.google["a"] = _review("a")  # Google recovered
        second = await self.run_backfill()
        self.assertEqual(self.items(second)["a"]["outcome"], "PUBLISHED")

    async def test_live_run_after_dry_run_publishes(self):
        self.set_reviews("a")
        self._patch_settings(auto_reply_dry_run=True)
        first = await self.run_backfill()
        self.assertEqual(first["would_publish"], 1)
        self._patch_settings(auto_reply_dry_run=False)
        second = await self.run_backfill()
        self.assertEqual(second["published"], 1)
        self.publish.assert_called_once()

    async def test_retries_wait_the_retry_delay_times_attempt(self):
        self._patch_settings(automation_backfill_retry_delay_seconds=15)
        self.set_reviews("a")
        self.google["a"] = GoogleAPIError("Service unavailable", status=503)
        with mock.patch.object(backfill, "_pause", new=mock.AsyncMock()) as pause:
            await self.run_backfill()
        self.assertEqual([c.args[1] for c in pause.await_args_list], [15, 30])

    async def test_new_backfill_allowed_after_completion(self):
        self.set_reviews("a")
        await self.run_backfill()
        self.google["a"] = _review("a", replied=True)
        summary = await self.run_backfill()
        self.assertEqual(summary["total"], 0)  # nothing pending any more
        self.publish.assert_called_once()

    async def test_disabled_automation_rejects_start(self):
        self._patch_settings(auto_reply_enabled=False)
        self.set_reviews("a")
        with self.assertRaises(BackfillRejected) as ctx:
            await backfill.start_backfill(LOCATION_ID)
        self.assertIn("AUTO_REPLY_ENABLED", ctx.exception.reason)
        self.list_pending.assert_not_called()

    # 12
    async def test_location_outside_allowlist_is_rejected(self):
        self._patch_settings(auto_reply_location_ids="other-location")
        self.set_reviews("a")
        with self.assertRaises(BackfillRejected) as ctx:
            await backfill.start_backfill(LOCATION_ID)
        self.assertEqual(ctx.exception.status_code, 403)
        self.list_pending.assert_not_called()

    async def test_default_location_resolving_outside_allowlist_is_rejected(self):
        self._patch_settings(auto_reply_location_ids="other-location")
        self.set_reviews("a")
        with self.assertRaises(BackfillRejected):
            await backfill.start_backfill(None)
        self.assertIsNone(backfill._active_job_id)
        self.get_review.assert_not_called()

    async def test_allowlisted_location_runs(self):
        self._patch_settings(auto_reply_location_ids=LOCATION_ID)
        self.set_reviews("a")
        summary = await self.run_backfill()
        self.assertEqual(summary["published"], 1)

    async def test_latest_job_is_filtered_by_location(self):
        self.set_reviews("a")
        summary = await self.run_backfill()
        self.assertEqual(backfill.latest_job(LOCATION_ID).job_id, summary["job_id"])
        self.assertIsNone(backfill.latest_job("another-location"))

    async def test_listing_failure_releases_the_slot(self):
        self.list_pending.side_effect = GoogleAPIError("boom", status=500)
        with self.assertRaises(GoogleAPIError):
            await backfill.start_backfill(LOCATION_ID)
        self.assertIsNone(backfill._active_job_id)
        self.assertEqual(backfill._jobs, {})

    async def test_cancel_stops_before_next_review(self):
        self.set_reviews("a", "b", "c")
        started = threading.Event()
        release = threading.Event()

        def blocking_get_review(review_id, location_id=None):
            started.set()
            release.wait(5)
            return self.google[review_id]

        self.get_review.side_effect = blocking_get_review
        job = await backfill.start_backfill(LOCATION_ID)
        await asyncio.to_thread(started.wait, 5)
        backfill.cancel_backfill(job.job_id)
        release.set()
        await backfill.wait_for_job(job.job_id, timeout=10)
        summary = job.summary()
        self.assertEqual(summary["status"], "CANCELLED")
        # The in-progress review finished normally; the rest never started.
        self.assertEqual(self.items(summary)["a"]["outcome"], "PUBLISHED")
        self.assertEqual(summary["cancelled"], 2)
        self.assertEqual([c.args[0] for c in self.publish.call_args_list], ["a"])


# --- Unanswered-review listing --------------------------------------------------------
class PendingListingTests(BackfillTestCase):
    # 2
    async def test_pending_list_uses_the_unanswered_rule_of_get_reviews(self):
        raw = [_review("a"), _review("b", replied=True), _review("c"),
               {**_review("d"), "reviewReply": {"comment": "   "}}]
        client = mock.MagicMock()
        with mock.patch("app.automation.backfill.get_google_client", return_value=client), \
                mock.patch(
                    "app.automation.backfill.get_location_name",
                    return_value=f"accounts/acct1/locations/{LOCATION_ID}",
                ) as location_name, \
                mock.patch.object(google_reviews, "get_reviews", return_value=raw):
            location, review_ids = _REAL_LIST_PENDING(None)
        location_name.assert_called_once_with(client, None)
        self.assertEqual(location, LOCATION_ID)
        self.assertEqual(review_ids, ["a", "c", "d"])  # blank reply counts as unanswered


# --- HTTP API --------------------------------------------------------------------------
class BackfillApiTests(BackfillTestCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(automation_api.router)
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.http.aclose()

    async def start(self, location: str | None = LOCATION_ID):
        params = {"location_id": location} if location else {}
        return await self.http.post("/automation/backfill", params=params)

    # 10 / 11
    async def test_start_then_poll_until_completed(self):
        self.set_reviews("a", "b")
        response = await self.start()
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(
            {k: body[k] for k in ("started", "location_id", "total_reviews", "dry_run")},
            {"started": True, "location_id": LOCATION_ID, "total_reviews": 2, "dry_run": False},
        )
        await backfill.wait_for_job(body["job_id"], timeout=10)
        status = (await self.http.get(f"/automation/backfill/{body['job_id']}")).json()
        self.assertEqual(status["status"], "COMPLETED")
        self.assertEqual((status["total"], status["processed"], status["published"]), (2, 2, 2))
        self.assertNotIn(GOOD_REPLY, str(status))  # no reply text in status
        latest = (await self.http.get(f"/automation/backfill?location_id={LOCATION_ID}")).json()
        self.assertEqual(latest["job"]["job_id"], body["job_id"])

    async def test_no_job_yet(self):
        self.assertEqual((await self.http.get("/automation/backfill")).json(), {"job": None})
        self.assertEqual((await self.http.get("/automation/backfill/nope")).status_code, 404)

    async def test_duplicate_start_returns_409_with_running_job(self):
        self.set_reviews("a")
        release = threading.Event()
        self.get_review.side_effect = lambda rid, location_id=None: (
            release.wait(5), self.google[rid])[1]
        first = (await self.start()).json()
        response = await self.start()
        release.set()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"started": False, "reason": "A backfill job is already running.",
             "job_id": first["job_id"]},
        )
        await backfill.wait_for_job(first["job_id"], timeout=10)

    async def test_empty_list_response(self):
        response = await self.start()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["started"])
        self.assertEqual(body["total_reviews"], 0)
        self.assertIn("no pending reviews", body["reason"])

    async def test_start_and_cancel_need_no_key(self):
        self.set_reviews("a")
        release = threading.Event()
        self.get_review.side_effect = lambda rid, location_id=None: (
            release.wait(5), self.google[rid])[1]
        response = await self.start()
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        cancel = await self.http.post(f"/automation/backfill/{job_id}/cancel")
        self.assertEqual(cancel.status_code, 200)
        self.assertTrue(cancel.json()["cancel_requested"])
        release.set()
        await backfill.wait_for_job(job_id, timeout=10)
        self.assertEqual(
            (await self.http.post("/automation/backfill/unknown/cancel")).status_code, 404
        )

    async def test_google_errors_when_listing(self):
        self.list_pending.side_effect = GoogleAPIError("Google API request failed", status=500)
        self.assertEqual((await self.start()).status_code, 502)
        from app.auth.google_oauth import GoogleOAuthError

        self.list_pending.side_effect = GoogleOAuthError("OAuth not completed")
        self.assertEqual((await self.start()).status_code, 401)

    async def test_disabled_automation_returns_409(self):
        self._patch_settings(auto_reply_enabled=False)
        response = await self.start()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["started"])

    async def test_automation_status_reports_backfill_delay(self):
        body = (await self.http.get("/automation/status")).json()
        self.assertEqual(body["backfill_delay_seconds"], 0)

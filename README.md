# Google Review Reply AI — Phase 1

**Live deployment**

- Frontend: <https://review-reply-ai-frontend.onrender.com>
- Backend: <https://kaurthreads.duckdns.org/>

A FastAPI application for a **boutique business** that:

1. Authenticates the owner with **Google OAuth 2.0** (Business Profile management scope).
2. Finds the boutique's Business Profile location through the Google Business Profile APIs.
3. Fetches Google reviews and returns **only the ones without a business reply**.
4. Generates a professional, boutique-appropriate reply with the **Groq API**, falling
   back automatically to the **Google Gemini API** if Groq fails.
5. Lets the user **edit** the reply and **publish it only after explicit approval** — with a **final reply check on Google** right before publishing.

> **Safety rule:** in the manual workflow, publishing happens only when the user
> explicitly calls the publish endpoint with the final approved text, and only if
> the review still has no business reply on Google at that moment.
>
> **Optional automation:** new reviews can also be answered automatically
> (Pub/Sub webhook → generate → validate → final Google check → publish). It is
> **off by default** (`AUTO_REPLY_ENABLED=false`) and has a dry-run mode. See
> [section 13](#13-automatic-review-reply-automation).

---

## 1. Architecture overview

```
Google Business Profile
        │
        ▼
app/auth/google_oauth.py        (OAuth 2.0: authorize URL, callback, credential cache/refresh)
app/auth/token_store.py         (encrypted credential storage in PostgreSQL)
        │
        ▼
app/google/client.py            (authenticated Business Profile REST client)
        │
        ▼
app/google/reviews.py           (location resolution, review fetching,
        │                          unanswered filtering, final check, publishing)
        ▼
GET /reviews                    (unanswered reviews only)
        │
        ▼
User selects a review
        │
        ▼
app/ai/reply_generator.py       (Groq primary → Gemini fallback, shared prompt)
        │
        ▼
Generated reply draft           (NOT published)
        │
        ▼
User reviews / edits
        │
        ▼
User explicitly approves  ──►  POST /reviews/{review_id}/publish
                                      │
                                      ▼
                              final reply check on Google
                                      │
                              ┌───────┴───────┐
                         reply exists    still unanswered
                              │               │
                              ▼               ▼
                        409, stop      publish approved text
```

The Google APIs used (all documented by Google, all authorized with the single
`https://www.googleapis.com/auth/business.manage` scope):

| API | Base URL | Used for |
| --- | --- | --- |
| Reviews API (mybusiness v4) | `https://mybusiness.googleapis.com/v4` | list/get reviews, `updateReply` (PUT `{name}/reply`) |
| My Business Account Management API (v1) | `https://mybusinessaccountmanagement.googleapis.com/v1` | `GET /accounts` (find the account) |
| My Business Business Information API (v1) | `https://mybusinessbusinessinformation.googleapis.com/v1` | `GET /{parent=accounts/*}/locations` (find the location) |

The review resource's `starRating` enum is normalized to a number
(`FIVE → 5`, `FOUR → 4`, …). A review counts as **answered** when its
`reviewReply.comment` is non-empty — Google is the source of truth; no local
state is stored.

## 2. Folder structure

```
google-review-reply/
├── app/
│   ├── main.py                  # FastAPI app, routers, health endpoint
│   ├── config.py                # env-based settings
│   ├── api/
│   │   ├── reviews.py           # GET /reviews, POST .../generate, .../validate, .../publish
│   │   └── automation.py        # GET /automation/status, dev-only POST /automation/test/{id}
│   ├── automation/
│   │   ├── processor.py         # automatic workflow: process_new_review() (shared by every trigger)
│   │   ├── resolver.py          # NEW_REVIEW → exact review, or fallback resolution from Google
│   │   ├── reconcile.py         # scheduled safety net: POST /automation/reconcile
│   │   └── backfill.py          # bulk "reply to all pending reviews"
│   ├── webhooks/
│   │   ├── google_reviews.py    # POST /webhooks/google-reviews (Pub/Sub push)
│   │   └── pubsub.py            # push JWT verification + notification decoding
│   ├── auth/
│   │   ├── google_oauth.py      # OAuth 2.0 only (authorize, callback, refresh) + auth endpoints
│   │   ├── token_store.py       # encrypted credential storage in PostgreSQL (DATABASE_URL)
│   │   └── import_token.py      # one-time import of an existing token file
│   ├── google/
│   │   ├── client.py            # authenticated Business Profile REST client
│   │   ├── reviews.py           # review fetching/filtering/final check/publish
│   │   └── resource_names.py    # tolerant review/location resource-name parsing
│   ├── ai/
│   │   ├── prompts.py           # shared reply-generation prompt (single source of truth)
│   │   ├── groq_client.py       # Groq integration (primary provider)
│   │   ├── gemini_client.py     # Gemini integration (fallback provider)
│   │   ├── reply_generator.py   # Groq → Gemini fallback orchestration
│   │   └── reply_validator.py   # manual "Check Reply" validation (Groq → Gemini fallback)
│   └── schemas/
│       └── review.py            # Pydantic API contracts
├── credentials/                 # gitignored; legacy token file location (import source only)
├── .env                         # gitignored; real local secrets
├── .env.example                 # committed template with placeholders
├── .gitignore
├── requirements.txt
└── README.md
```

## 3. Required Google setup (Google-side configuration)

This code does **not** work until the Google-side configuration below is done.
Code setup and Google Cloud setup are separate: even a perfect project still
needs an approved, enabled API and an OAuth client.

1. **Google Cloud project**: create one at <https://console.cloud.google.com>.
2. **Enable APIs** for that project:
   - *Google Business Profile API* (provides the Reviews API on
     `mybusiness.googleapis.com/v4` — reviews are still served by the v4
     endpoint),
   - *My Business Account Management API*,
   - *My Business Business Information API*.
3. **Request access / quota**: the Google Business Profile APIs start with a
   quota of **0** and require an access request. Use
   **Business Profile API → Request API access** (or the
   [My Business API request form](https://support.google.com/mybusiness/answer/10440015)).
   Mention your verified Business Profile and that you only manage your own
   location's reviews. Until Google grants quota, calls return 403/429.
4. **OAuth consent screen**: set up the OAuth consent screen (External for a
   personal business, or Internal for a Workspace domain). No sensitive
   scopes are used, so verification/publishing is not required for local use.
5. **OAuth client (Web application)**: create an *OAuth client ID* of type
   **Web application** and add the authorized redirect URI
   `http://localhost:8000/auth/google/callback` (or whatever you configure in
   `GOOGLE_REDIRECT_URI`). Copy the client ID and secret into `.env`.
6. **Account ownership**: the Google account you sign in with must own or
   manage the boutique's Business Profile, and the Business Profile must be
   **verified** (review reply operations only work on verified locations).

> The OAuth scope used is `https://www.googleapis.com/auth/business.manage`,
> which covers all three APIs above with a single token.

## 4. Required environment variables

Copy `.env.example` to `.env` and fill it in:

| Variable | Required | Description |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | yes | OAuth client ID (Web application). |
| `GOOGLE_CLIENT_SECRET` | yes | OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | yes | Must match the redirect URI registered with the OAuth client. Default `http://localhost:8000/auth/google/callback`. |
| `GROQ_API_KEY` | yes* | Groq API key from <https://console.groq.com/keys> (primary provider). |
| `GEMINI_API_KEY` | yes* | Gemini API key from <https://aistudio.google.com/apikey> (fallback for generation and Check Reply). |
| `GOOGLE_LOCATION_ID` | no | Pin a specific Business Profile location (bare location ID or `accounts/{account}/locations/{location}`). Default: first location of the first account. |
| `GROQ_MODEL` | no | Groq model for generation. Default `llama-3.3-70b-versatile`. |
| `GEMINI_MODEL` | no | Gemini model for fallback generation. Default `gemini-3.8-flash`. |
| `DATABASE_URL` | yes | PostgreSQL connection string (Neon in production) where the Google OAuth credentials are stored. |
| `GOOGLE_TOKEN_ENCRYPTION_KEY` | yes | Fernet key encrypting the stored credentials. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Comma-separate `new,old` to rotate. |
| `GOOGLE_TOKEN_FILE` | no | Legacy token file; no longer read or written at runtime. Only the default source of `python -m app.auth.import_token`. |
| `AUTO_REPLY_ENABLED` | no | `true` enables automatic replies to new reviews. Default **`false`**. |
| `AUTO_REPLY_DRY_RUN` | no | `true` runs the full automatic pipeline without publishing (`WOULD_PUBLISH` log). Default `false`. |
| `AUTO_REPLY_MAX_REGENERATIONS` | no | Regenerations after a failed validation: `0` or `1` (hard maximum). Default `1`. |
| `AUTO_REPLY_LOCATION_IDS` | no | Comma-separated location ids to automate. Empty = every location of the notifying account. |
| `PUBSUB_PUSH_AUDIENCE` | for automation | Expected `aud` of the Pub/Sub push JWT. |
| `PUBSUB_PUSH_SERVICE_ACCOUNT` | for automation | Expected `email` of the Pub/Sub push JWT. |
| `AUTOMATION_TEST_ENDPOINT_ENABLED` | no | Development only: exposes `POST /automation/test/{review_id}`. Default `false`. |
| `AUTOMATION_BACKFILL_DELAY_SECONDS` | no | Pause between reviews of a bulk reply (rate limits). Default `2`, max `60`. |
| `AUTOMATION_BACKFILL_RETRY_DELAY_SECONDS` | no | Base wait before retrying a review after a transient failure, multiplied by the attempt number. Default `15`, max `300`. |

\* At least one of `GROQ_API_KEY` / `GEMINI_API_KEY` is required; set both for
automatic fallback.

The Google OAuth token is **not** an environment variable. After the first
successful OAuth flow it is stored, encrypted with
`GOOGLE_TOKEN_ENCRYPTION_KEY`, in the `google_oauth_credentials` table of
`DATABASE_URL` (created automatically at startup), and refreshed tokens are
saved there too. The OAuth client secret is never stored in the database.
There is no file fallback: if the database is unavailable, OAuth returns an
error instead of saving the token somewhere temporary. For local development,
use a separate Neon branch (or local PostgreSQL) — authorizing locally against
the production database replaces the production credentials.

## 5. Groq API setup

1. Create a Groq account and an API key at <https://console.groq.com/keys>.
2. Put the key in `GROQ_API_KEY` in `.env`.
3. (Optional) change `GROQ_MODEL` — any chat model available to your key works
   (default: `llama-3.3-70b-versatile`).

### Gemini fallback

If Groq fails for any reason (missing key, API error, rate limit / HTTP 429,
timeout, empty reply), the same request is sent **once** to Gemini with the
exact same prompt and rules. Gemini is never called when Groq succeeds. If
Gemini also fails (or `GEMINI_API_KEY` is empty), the generate endpoint returns
a clean `502`.

1. Create an API key at <https://aistudio.google.com/apikey>.
2. Put it in `GEMINI_API_KEY` in `.env`.
3. (Optional) change `GEMINI_MODEL` (default: `gemini-3.8-flash`).

## 6. Local installation

```bash
cd google-review-reply
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit .env with real values
```

## 7. How to run

```bash
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: <http://localhost:8000/docs>
- OpenAPI JSON: <http://localhost:8000/openapi.json>
- Health: <http://localhost:8000/health>

## 8. How to complete Google OAuth

1. `GET /auth/google/status` — shows `authenticated: false` initially.
2. `GET /auth/google/authorize` — returns `authorization_url`.
   Open that URL in a **browser on the same machine** as the server, sign in
   with the Google account that manages the boutique, and approve the
   `business.manage` scope.
3. Google redirects the browser to
   `http://localhost:8000/auth/google/callback?code=...&state=...` — the app
   exchanges the code for a token (with refresh token) and stores it in the
   database. The callback page shows the result.
4. `GET /auth/google/status` now shows `authenticated: true`.

Notes:

- The redirect must land on `http://localhost:8000` in a browser that can reach
  the server, so run this flow on the machine where the app runs.
- To switch Google accounts, Disconnect (`POST /auth/google/logout` deletes
  the stored credentials) and repeat the flow. A new authorization always
  replaces the stored one.

## 9. API usage examples

### List unanswered reviews

```
GET /reviews
```

```json
[
  {
    "review_id": "AIe9_BFu3rdicGrPrzdyu4...",
    "reviewer": "Customer",
    "rating": 4,
    "review": "Nice shop, but the sizes ran small.",
    "created_at": "2026-09-01T10:00:00Z",
    "has_reply": false
  }
]
```

Reviews that already have a business reply are **never** returned here.

### Generate a reply (draft only — nothing is published)

```
POST /reviews/{review_id}/generate
```

```json
{
  "review_id": "AIe9_BFu3rdicGrPrzdyu4...",
  "reply": "Thank you for taking the time to share this with us! We're sorry the fit wasn't quite right — we'd love to make it right. Please reach out and we'll happily help you find a size that works.",
  "review": { "…": "normalized review details" }
}
```

Returns `409` if the review already has a business reply.

### Check a draft reply (manual validation — nothing is published)

```
POST /reviews/{review_id}/validate?location_id=...   (location_id optional)
{ "reply": "Draft reply currently shown to the user" }
```

Triggered only by the **Check Reply** button on the review page. It answers
"is this draft *clearly* unsuitable or unsafe to publish?" — it is a lenient,
high-confidence check, not a writing-quality score, so a natural or slightly
generic boutique reply should pass. It is for observing validator behaviour;
**Post Reply does not require a PASS**, and nothing is regenerated or
published automatically.

The backend re-fetches the review from Google (the frontend only sends the
draft), then:

1. `409` if the review already has a business reply (Gemini is not called).
2. Cheap deterministic checks — empty text, over Google's 4096-byte limit,
   broken characters, unfilled placeholders like `[Customer Name]`, obvious
   AI/prompt disclosure ("As an AI…"). A problem here returns `FAIL` without
   calling any AI provider.
3. Otherwise the same provider order as reply generation: **one** Groq call
   (`GROQ_MODEL`) returns the structured verdict; only if Groq fails (missing
   key, rate limit, timeout, unreadable answer, …) is **one** Gemini call
   (`GEMINI_MODEL`) made instead. No retries.

```json
{
  "review_id": "AIe9_BFu3rdicGrPrzdyu4...",
  "passed": false,
  "decision": "FAIL",
  "reason": "The reply refers to a restaurant meal, not a fashion boutique.",
  "checks": {
    "review_relevance": false,
    "business_relevance": false,
    "no_hallucination": true,
    "appropriate_tone": true,
    "safe_to_publish": false
  }
}
```

On `PASS`, `passed` is `true`, `reason` is `null` and every check is `true`.
Needs `GROQ_API_KEY` and/or `GEMINI_API_KEY`. If every configured provider
fails the endpoint returns `502` with a user-safe message, or `503` when the
cause is a usage limit / overloaded provider (the message says which).

### Publish the user-approved final reply

```
POST /reviews/{review_id}/publish
{ "reply": "Final reply written or edited by the user" }
```

The backend **re-fetches the review from Google first**:

- if a business reply now exists → `409`, nothing is published;
- if not → the exact supplied text is published via the Reviews API
  (`PUT .../reviews/{review_id}/reply`) and a `PublishResult` is returned.

The backend never assumes the AI draft is the final text: whatever is sent in
`reply` is what gets published.

### Auth helpers

```
GET /auth/google/status      # is the app authenticated?
GET /auth/google/authorize   # get the Google authorization URL
GET /auth/google/callback    # OAuth redirect target (code + state)
```

## 10. Safety and approval behavior

- **Manual publishing needs explicit approval.** In the manual workflow only
  an explicit `POST /reviews/{review_id}/publish` publishes, and only with
  user-supplied final text.
- **Automatic publishing is opt-in.** The only automatic code path is the
  Pub/Sub webhook (section 13). It does nothing unless
  `AUTO_REPLY_ENABLED=true`, never publishes in `AUTO_REPLY_DRY_RUN=true`, and
  publishes only a reply that passed validation and a final Google check.
- **Final check before publish.** The review is re-fetched from Google
  immediately before publishing; a reply added by anyone in the meantime
  blocks the publish with `409`.
- **Answered reviews are invisible to the workflow.** `GET /reviews` filters
  them out, and `generate`/`publish` refuse them with `409`.
- **AI guardrails.** The Groq prompt forbids inventing facts, policies,
  refunds, discounts, employee names, or causes; forbids arguing, blaming,
  legal/medical claims, and private data; requires concise, warm,
  boutique-appropriate replies that address the actual review content; and for
  1–2 star reviews requires a cautious, non-admitting, professional response
  with no invented resolution.
- **Validation.** Publish text must be non-empty and at most 4096 bytes
  (Google's reply limit). Rating-only reviews (no written comment) can be
  generated for, with the prompt told the review has no text.
- **Concurrency caveat (Phase 1):** two simultaneous publish requests for the
  same review could both pass the final check; the second `updateReply` would
  replace the first. This tool is single-user by design; serialize publishing.
  The same applies to a manual publish racing an automatic one: the automatic
  run checks Google immediately before publishing, but a reply posted between
  that check and the publish call can be replaced.

## 11. Security notes

- All secrets live in `.env` (gitignored) — never in code or Git.
- `.gitignore` excludes `.env`, `credentials/`, virtualenvs, and bytecode.
- OAuth credentials are stored Fernet-encrypted in PostgreSQL (without the
  client secret); they are never returned by any endpoint (auth status
  returns only booleans, versions, timestamps and expiry) and never logged.
- Error messages are sanitized: they include Google's error text where useful
  but never tokens, secrets, or credential file contents.
- Tokens are refreshed automatically on expiry and on 401 (once, then the
  error surfaces).

## 12. Google-side prerequisites / limitations (affects local testing)

- **API access approval**: until Google grants quota for the Business Profile
  APIs, every Google call fails (403 "API has not been used in project or it
  is disabled" or 429 quota). This is a Google-side gate, not a code issue.
- **Verified location**: review replies only work for a **verified** Business
  Profile.
- **Single location**: Phase 1 targets one boutique location (first location
  of the first account, or the one pinned via `GOOGLE_LOCATION_ID`).
- **OAuth redirect**: the browser must reach `http://localhost:8000`, so the
  OAuth flow must be completed from a browser on the machine running the app.
- **v4 reviews endpoint**: reviews are still served by the legacy
  `mybusiness.googleapis.com/v4` endpoint (Google has not migrated reviews to
  v1); this app calls exactly the documented v4 methods
  (`list`, `get`, `updateReply`).

## 13. Automatic Review Reply Automation

New reviews can be answered automatically, in addition to (never instead of)
the manual workflow. Everything in the manual workflow keeps working
unchanged: Generate Reply, Check Reply, Edit Manually and Post Reply.

> **Status:** the code path is unit-tested with mocked Google/AI services. It
> is **not production-verified** until the Google Cloud configuration below
> is done, the deployed OAuth token is durable, and a dry run has been
> observed with a real new review.

### 13.1 Flow

```
New Google review
  → Business Profile notification (NEW_REVIEW)
  → Cloud Pub/Sub topic
  → authenticated HTTPS push → POST /webhooks/google-reviews
  → verify Pub/Sub JWT (signature, issuer, audience, service account, expiry)
  → decode notification (tolerant: field aliases, URL-encoding, slashes,
    full/short/bare review ids — see 13.12); the event is only a trigger
  → review reference usable? → that exact review (Google 404 → SKIPPED_NOT_FOUND)
    missing / unparseable → FALLBACK_RESOLUTION:
    newest unanswered review of the notified location (13.12)
  → AUTO_REPLY_ENABLED? no → DISABLED (acknowledged, nothing else happens)
  → fetch the review from Google (source of truth)
  → already replied? yes → SKIPPED_ALREADY_REPLIED (no AI call)
  → generate: Groq; Gemini only if Groq fails
  → validate: same validate_review_reply() as Check Reply
       (deterministic checks first, then one AI verdict)
  → FAIL? regenerate ONCE with the failure reason → validate again
       → FAIL again → FAILED_VALIDATION (stop; reply manually)
  → final Google check: replied meanwhile? → SKIPPED_ALREADY_REPLIED
  → AUTO_REPLY_DRY_RUN? yes → DRY_RUN (logs WOULD_PUBLISH + the reply)
  → publish_reply() (same function as Post Reply) → PUBLISHED
  → verification read: Google shows the reply? (logged as verified=…)
```

The scheduled reconciliation (13.11) and the bulk backfill (13.10) feed
reviews into the very same `process_new_review()`; there is only one
reply pipeline.

A reply is published only when **all** of these hold: `AUTO_REPLY_ENABLED`
is true, dry run is off, the review was unanswered, generation succeeded, the
deterministic checks and the AI verdict both passed (for the regenerated
reply too, when regeneration was needed), and the final Google check found no
business reply.

**AI call budget per review:** normally 1 generation + 1 validation. At most
2 generations (+1 Gemini fallback each) and 2 validations (+1 Gemini
fallback each). Never a third generation. A validator *outage* (timeout,
quota, malformed provider answer) is **not** a FAIL verdict: the run stops as
a retryable `ERROR` without regenerating.

Note: the existing validator (`app/ai/reply_validator.py`) asks **Groq
first and Gemini only if Groq fails** — the same order as generation. The
automation reuses it unchanged.

### 13.2 Pub/Sub acknowledgement semantics

| Outcome | HTTP | Pub/Sub |
| --- | --- | --- |
| `PUBLISHED`, `DRY_RUN`, `SKIPPED_ALREADY_REPLIED`, `FAILED_VALIDATION`, `DISABLED`, `IGNORED` (other notification types / location not allowlisted), `SKIPPED_NOT_FOUND`, `SKIPPED_NO_UNANSWERED_REVIEW` (fallback found nothing to do), recently-finished `DUPLICATE` | 200 | acknowledged |
| Same review (or same fallback message) already being processed (`DUPLICATE`) | 409 | redelivered later |
| Transient Google/AI failure (`ERROR`, `FAILED_GENERATION`), incl. a failed fallback listing | 503 | redelivered |
| Undecodable message, or NEW_REVIEW with **no valid location** / location contradicting the review (`REJECTED_INVALID_MESSAGE`) | 200 | acknowledged (logged as `WEBHOOK_REJECTED`) |
| Synthetic / foreign location (non-numeric id such as `TEST_LOCATION`, or not `GOOGLE_LOCATION_ID`) — `IGNORED` before any Google call; location not in the Google account — `ERROR`, not retryable | 200 | acknowledged |
| Missing / invalid JWT | 401 / 403 | redelivered (fix the configuration) |

A NEW_REVIEW whose review value has an unexpected format is **not** a 400
any more as long as its location is valid: it is resolved from Google
(13.12). Retries are bounded twice: the subscription's dead-letter policy
(below), and in the app, which acknowledges a review after **3** retryable
failures and logs "manual review needed" (a fallback message whose Google
listing keeps failing is likewise acknowledged after 3 attempts — the
reconciliation then picks the review up).

### 13.3 Duplicate handling

Pub/Sub can deliver a message more than once. Inside the process:

1. a per-review `asyncio.Lock`: a second delivery while the review is being
   processed gets `409` and is retried later;
2. an outcome memory (24 h): a redelivery after `FAILED_VALIDATION`,
   `DRY_RUN` or an exhausted retry budget is acknowledged without new AI calls;
3. the **mandatory** Google check: a redelivery after `PUBLISHED` fetches the
   review, sees the reply and stops; the final check before publishing
   catches replies made by anyone while the AI was working;
4. fallback messages are remembered by Pub/Sub message id (24 h): a
   redelivered fallback notification is acknowledged as `DUPLICATE` instead
   of picking the *next* unanswered review.

The webhook, the reconciliation and the bulk backfill share the per-review
lock and outcome memory, so they never process the same review at the same
time; the fallback resolver and the reconciliation skip reviews that are in
progress or recently settled before choosing what to process. Google's
`reviewReply` (read at the start and immediately before publishing) is the
final authority — the in-memory state only saves AI calls.

**Run the backend as one process.** The lock and memory live in memory.
Use a single Render instance and a single uvicorn worker (the default:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`, no `--workers N`, no
multi-worker gunicorn). With several workers or instances, the Google checks
still prevent most duplicates, but two processes could both pass the final
check at the same moment. That needs a shared lock (e.g. Redis or Postgres)
before you scale out. A restart clears the memory (the Google checks still
apply).

### 13.4 Google Cloud configuration (done in Google Cloud / Business Profile)

Verified against Google's current documentation: the **My Business
Notifications API v1** (`mybusinessnotifications.googleapis.com`,
resource `accounts/{accountId}/notificationSetting`, notification type
`NEW_REVIEW`) — not the old v4 `updateNotifications` method — plus Cloud
Pub/Sub authenticated push.

Replace `PROJECT_ID`, `PROJECT_NUMBER` and `YOUR-RENDER-BACKEND-DOMAIN`.

1. **Google Cloud project**: use the project that holds the OAuth client and
   has approved Business Profile API access (section 3).
2. **Enable the APIs**:
   ```bash
   gcloud services enable pubsub.googleapis.com \
     mybusinessnotifications.googleapis.com --project=PROJECT_ID
   ```
   (The Business Profile, Account Management and Business Information APIs
   from section 3 must also be enabled.)
3. **Pub/Sub topic**, and let Business Profile publish to it:
   ```bash
   gcloud pubsub topics create gbp-reviews --project=PROJECT_ID
   gcloud pubsub topics add-iam-policy-binding gbp-reviews --project=PROJECT_ID \
     --member=serviceAccount:mybusiness-api-pubsub@system.gserviceaccount.com \
     --role=roles/pubsub.publisher
   ```
4. **Push-auth service account** (Pub/Sub signs the push JWT as this account):
   ```bash
   gcloud iam service-accounts create gbp-reviews-push --project=PROJECT_ID \
     --display-name="GBP reviews push auth"
   # Pub/Sub service agent may mint tokens for it (granted by default only on
   # projects created after 2021-04-08; harmless to add):
   gcloud iam service-accounts add-iam-policy-binding \
     gbp-reviews-push@PROJECT_ID.iam.gserviceaccount.com \
     --member=serviceAccount:service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com \
     --role=roles/iam.serviceAccountTokenCreator
   ```
   Whoever creates the subscription needs `iam.serviceAccounts.actAs` on that
   account (e.g. `roles/iam.serviceAccountUser`).
5. **Dead-letter topic** (collects messages that keep failing):
   ```bash
   gcloud pubsub topics create gbp-reviews-dead-letter --project=PROJECT_ID
   gcloud pubsub topics add-iam-policy-binding gbp-reviews-dead-letter --project=PROJECT_ID \
     --member=serviceAccount:service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com \
     --role=roles/pubsub.publisher
   ```
6. **Authenticated push subscription** to the Render HTTPS URL:
   ```bash
   gcloud pubsub subscriptions create gbp-reviews-push --project=PROJECT_ID \
     --topic=gbp-reviews \
     --push-endpoint=https://YOUR-RENDER-BACKEND-DOMAIN/webhooks/google-reviews \
     --push-auth-service-account=gbp-reviews-push@PROJECT_ID.iam.gserviceaccount.com \
     --push-auth-token-audience=https://YOUR-RENDER-BACKEND-DOMAIN/webhooks/google-reviews \
     --ack-deadline=600 \
     --min-retry-delay=60s --max-retry-delay=600s \
     --dead-letter-topic=gbp-reviews-dead-letter --max-delivery-attempts=5
   gcloud pubsub subscriptions add-iam-policy-binding gbp-reviews-push --project=PROJECT_ID \
     --member=serviceAccount:service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com \
     --role=roles/pubsub.subscriber
   ```
   - The endpoint must be the public **HTTPS** Render URL — never `localhost`.
   - **Ack deadline:** a push subscription waits for the HTTP response only up
     to its acknowledgement deadline (default 10 s). One run makes up to
     3 Google operations (fetch, final check, publish — each first resolves
     the location, ~9 HTTP calls), up to 4 generation and 4 validation
     calls, with 30 s timeouts each.
     A normal run takes a few seconds, but use the 600 s maximum so slow
     provider responses are not cut off and redelivered while still running.
   - Retry delay 60–600 s plus 5 delivery attempts bound retries for
     transient failures.
7. **Business Profile notification setting** (per Business Profile *account*,
   authorized as the Business Profile owner with the `business.manage`
   token this app already holds). Run once, locally, from the project root
   after completing OAuth:
   ```bash
   python - <<'PY'
   import requests
   from app.auth.google_oauth import load_credentials
   from app.google.client import get_google_client

   TOPIC = "projects/PROJECT_ID/topics/gbp-reviews"
   account = get_google_client().list_accounts()["accounts"][0]["name"]
   url = f"https://mybusinessnotifications.googleapis.com/v1/{account}/notificationSetting"
   headers = {"Authorization": f"Bearer {load_credentials().token}"}
   body = {"name": f"{account}/notificationSetting",
           "pubsubTopic": TOPIC, "notificationTypes": ["NEW_REVIEW"]}
   r = requests.patch(url, params={"updateMask": "pubsubTopic,notificationTypes"},
                      headers=headers, json=body, timeout=30)
   print(r.status_code, r.text)
   print(requests.get(url, headers=headers, timeout=30).text)  # verify
   PY
   ```
   This **replaces** the account's notification types: if the account already
   subscribes to other types, include them in `notificationTypes`. The
   setting covers every location of the account, so use
   `AUTO_REPLY_LOCATION_IDS` to restrict automation to specific locations.

### 13.5 Application configuration (Render environment variables)

| Variable | Value |
| --- | --- |
| `PUBSUB_PUSH_AUDIENCE` | exactly the `--push-auth-token-audience` above |
| `PUBSUB_PUSH_SERVICE_ACCOUNT` | `gbp-reviews-push@PROJECT_ID.iam.gserviceaccount.com` |
| `DATABASE_URL` | Neon connection string (see 13.6) |
| `GOOGLE_TOKEN_ENCRYPTION_KEY` | Fernet key (see section 4 and 13.6) |
| `AUTO_REPLY_ENABLED` | `false` → then `true` (see 13.7) |
| `AUTO_REPLY_DRY_RUN` | `true` first, `false` once dry runs look right |
| `AUTO_REPLY_LOCATION_IDS` | optional allowlist |
| `AUTOMATION_TEST_ENDPOINT_ENABLED` | `false` in production |
| `AUTOMATION_BACKFILL_DELAY_SECONDS` | optional, default `2` |
| `AUTOMATION_BACKFILL_RETRY_DELAY_SECONDS` | optional, default `15` |
| `AUTO_REPLY_VERIFY_AFTER_PUBLISH` | optional, default `true` (re-read the review after publishing) |
| `RECONCILIATION_ENABLED` | optional, default `true` |
| `RECONCILIATION_SECRET` | **required for reconciliation** (16+ chars; `python -c "import secrets; print(secrets.token_urlsafe(32))"`) |
| `RECONCILIATION_MAX_REVIEWS` | optional, default `10` per run |
| `RECONCILIATION_LOOKBACK_MINUTES` | optional, default `10080` (7 days); also bounds the webhook fallback; `0` = no limit |

The webhook rejects every request while either `PUBSUB_PUSH_*` value is
empty. The JWT is never logged.

### 13.6 Render deployment requirements

- **OAuth credentials live in PostgreSQL (Neon free tier is enough).** Set
  `DATABASE_URL` (the Neon connection string) and
  `GOOGLE_TOKEN_ENCRYPTION_KEY` on the service. The table is created at
  startup (idempotent). Complete the OAuth flow once through the deployed
  app (`GOOGLE_REDIRECT_URI` must be the Render backend's
  `/auth/google/callback`, registered on the OAuth client); the credentials
  then survive restarts, redeploys and free-plan spin-down, and refreshed
  tokens are saved automatically. No disk or Secret File is needed.
  **Keep the encryption key safe:** losing or changing it makes the stored
  credentials unreadable (re-authorize to recover).
- **Migrating an existing token file** (optional; re-authorizing is
  simpler): on a machine that has the file, with the production
  `DATABASE_URL`, `GOOGLE_TOKEN_ENCRYPTION_KEY`, `GOOGLE_CLIENT_ID` and
  `GOOGLE_CLIENT_SECRET` in the environment, run
  `python -m app.auth.import_token path/to/google_token.json`. It never
  overwrites stored credentials unless `--replace` is given, and never
  changes the file. `GOOGLE_TOKEN_FILE` (e.g. a Secret File at
  `/etc/secrets/google_token.json`) is ignored at runtime; remove it once
  the database holds working credentials.
- **Verify after every deploy.** The startup log line `Automatic replies:
  ...` ends with `token_store=postgresql database_configured=...
  encryption_key_configured=...`, and `GET /auth/google/status` reports (no
  token values) `database_configured`, `encryption_key_configured`,
  `database_reachable`, `credentials_stored`, `stored_version`,
  `stored_updated_at`, `refresh_token_present` and `refreshed_token_unsaved`.
  `GET /auth/google/status?verify=true` also makes one read-only Business
  Profile call (`google_api_ok`).
- **Expired access tokens are normal.** Access tokens last about an hour;
  the app refreshes them with the stored refresh token, saves the result,
  and reuses it until it expires again (one refresh per hour, not one per
  Google call). A refreshed token is only saved if nobody stored newer
  credentials meanwhile, so a stale instance never overwrites a new
  authorization. If the database is briefly unreachable, the in-memory
  credentials keep working and the save is retried. Re-authentication is
  only needed when the *refresh* token is revoked or expired.
- **Refresh tokens must not expire:** while the OAuth consent screen's
  publishing status is **Testing**, Google expires refresh tokens after
  7 days. Set it to **In production** for unattended use.
- **Single process / single instance** (see 13.3).
- **Free plan spin-down:** an idle free service sleeps after 15 minutes and
  takes about a minute to wake. Pub/Sub keeps retrying, so events are not
  lost, but a paid instance is more reliable.

### 13.7 Rollout and testing

1. Deploy with `AUTO_REPLY_ENABLED=false`. `GET /automation/status` shows
   `"enabled": false` and `"webhook_auth_configured": true`.
2. Publish a synthetic event (tests auth + decoding end to end; nothing is
   processed while disabled):
   ```bash
   gcloud pubsub topics publish gbp-reviews --project=PROJECT_ID --message='{"type":"NEW_REVIEW","review":"accounts/ACCOUNT/locations/LOCATION/reviews/REVIEW","location":"accounts/ACCOUNT/locations/LOCATION"}'
   ```
   Render logs show `automation ... status=DISABLED` and an `automation_run`
   JSON summary.
3. Set `AUTO_REPLY_ENABLED=true` and `AUTO_REPLY_DRY_RUN=true`. Repeat with a
   real **unanswered** review id: logs show `WOULD_PUBLISH` with the reply.
   Then wait for a real new review and check its logs. Google documents the
   notification's fields only informally (`reviewName` / `locationName`), so
   the decoder accepts the aliases and shapes in 13.12. A notification whose
   review value it cannot read is resolved from Google
   (`FALLBACK_RESOLUTION`); only a notification without any valid location
   is rejected (`WEBHOOK_REJECTED … Pub/Sub push rejected: …`, with the
   sanitized raw values) and ends in the dead-letter topic.
4. When dry runs look right, set `AUTO_REPLY_DRY_RUN=false`.

**Local development:** set `AUTOMATION_TEST_ENDPOINT_ENABLED=true` (with
`AUTO_REPLY_ENABLED=true` and `AUTO_REPLY_DRY_RUN=true`) and call
`POST /automation/test/{review_id}?location_id=...`. It runs the same
`process_new_review()` with every gate (only the "recently finished"
duplicate memory is skipped so the same review can be re-tested). It returns
404 unless the flag is on.

**Unit tests:** `pip install -r requirements-dev.txt`, then
`python -m unittest discover -s tests -t .` (automation tests:
`tests/test_automation.py`, all Google/AI calls mocked). The credential-store
tests (`tests/test_token_store.py`) run against a throwaway local PostgreSQL
started by `pgserver`, or `TEST_DATABASE_URL` if set; they never use
`DATABASE_URL`.

### 13.8 Run logs

Every status transition is logged as
`automation run=<id> review=<id> location=<id> status=<STATUS>`, and every
run ends with one `automation_run {json}` line containing review/location
ids, event type, generation and regeneration providers, validation results
and reasons, publish result, final status, error stage, and the (truncated)
reply text. No API keys, OAuth tokens, JWTs or authorization headers are
logged.

### 13.9 Limitations

- Only `NEW_REVIEW` triggers real-time automation. The reconciliation
  (13.11) catches reviews inside `RECONCILIATION_LOOKBACK_MINUTES` whose
  notification was late, lost or unusable; older reviews (e.g. from before
  automation was enabled) need a manual reply or the bulk reply (13.10).
- A review that ends in `FAILED_VALIDATION` or exhausts its retry budget is
  not retried by the reconciliation for 24 h (in-process memory; a restart
  clears it), so a broken AI provider cannot burn quota every few minutes.
- Location resolution uses the first Business Profile account (as the rest
  of the app does). Events for locations in other accounts fail and are
  acknowledged after the retry budget.
- Duplicate protection is per process (see 13.3).
- `FAILED_VALIDATION` and exhausted-retry reviews are visible only in the
  logs (no database); they stay in the dashboard's unanswered list for
  manual replies.

### 13.10 Bulk reply to all pending reviews (backfill)

The dashboard's **Reply to All Pending Reviews (N)** button answers every
review that currently has no business reply. It does not add a second
pipeline: `app/automation/backfill.py` calls the same
`process_new_review()` as the Pub/Sub webhook for each review, so the fresh
Google fetch, "already replied" check, generation (Groq → Gemini),
validation with at most one regeneration, final Google check,
`AUTO_REPLY_DRY_RUN` gate and per-review lock all apply unchanged.

```
POST /automation/backfill?location_id=…        start → 202 {job_id, total_reviews}
GET  /automation/backfill/{job_id}              progress / result (no reply text, no secrets)
GET  /automation/backfill?location_id=…         running job, else the most recent one (page-refresh recovery)
POST /automation/backfill/{job_id}/cancel       stop after the current review
```

- **Sequential:** one review at a time, `AUTOMATION_BACKFILL_DELAY_SECONDS`
  between reviews. A failed review is recorded and the batch continues.
- **Retries:** a retryable failure (Google 5xx/429, AI provider outage) is
  retried up to `MAX_PROCESSING_ATTEMPTS` (3), waiting
  `AUTOMATION_BACKFILL_RETRY_DELAY_SECONDS` × attempt (15 s, then 30 s) so
  per-minute AI quotas can recover. A validation FAIL after the one
  regeneration is final for that run (left for a manual reply).
- **Every click is a fresh try:** a bulk reply is an explicit user action, so
  it clears the processor's "recently finished" memory and retry budget for
  each review it processes. Reviews that failed (or were only dry-run) in an
  earlier run are processed again instead of being skipped as duplicates.
- **No double replies:** every attempt re-reads the review from Google and
  checks again right before publishing; only one backfill can run at a
  time (a second start returns `409 {"started": false, "reason": "A
  backfill job is already running.", "job_id": …}`); the per-review lock
  also blocks a webhook run for the same review.
- **Gates:** rejected while `AUTO_REPLY_ENABLED=false` or when the location
  is outside `AUTO_REPLY_LOCATION_IDS`. With `AUTO_REPLY_DRY_RUN=true` every
  review ends as `DRY_RUN` (logged `WOULD_PUBLISH`) and nothing is sent.
- **No extra authentication:** like the rest of this API (the app has no
  user accounts), the endpoints are open to anyone who can reach the
  backend. The dashboard asks for one confirmation click; `AUTO_REPLY_ENABLED`
  is the server-side off switch.
- **Job state is in memory** (no database, like the duplicate memory in
  13.3). A restart or deploy during a bulk reply loses the job record (the
  UI reports "interrupted") — but never causes double replies: published
  reviews drop out of the unanswered list, so starting again continues
  with the rest. Render's free plan spins down after 15 minutes without
  inbound requests; keep the dashboard open during long runs (its polling
  counts as traffic).
- **Re-runs within 24 h:** the processor remembers `FAILED_VALIDATION` and
  `DRY_RUN` outcomes per review for 24 h (13.3), so a second bulk reply in
  the same process reports those reviews as `SKIPPED` ("recently processed")
  instead of spending AI calls again. A deploy/restart clears this memory.
- **Logs:** `BACKFILL_STARTED job=… total=… location=… dry_run=…`,
  `BACKFILL job=… review=… status=PROCESSING`, `BACKFILL job=… review=…
  run=… status=PUBLISHED|DRY_RUN|SKIPPED_ALREADY_REPLIED|…`, `… status=FAILED
  final_status=… stage=…`, and `BACKFILL_COMPLETED job=… total=…
  published=… would_publish=… skipped=… failed=… cancelled=…`. Each review's
  processor lines (`automation run=…`) share its `run` id.

**Rollout:** set `AUTO_REPLY_DRY_RUN=true`, deploy, run the bulk reply, confirm on Google that nothing was posted and
that the logs show `WOULD_PUBLISH` for the reviews you expect. Then set
`AUTO_REPLY_DRY_RUN=false`, deploy (this also clears the in-memory
"recently dry-run" memory), and run it once.

### 13.11 Reconciliation (scheduled safety net)

Pub/Sub is the real-time path; reconciliation makes sure a review is not
left unanswered when a notification is delayed (Render asleep/restarting),
dropped after its retries, or unusable. It is **one bounded job per call**
— there is no background loop in the web process, so it works on a
stateless, restartable Render service. An external scheduler calls it.

```
POST /automation/reconcile[?location_id=…][&wait=true]   start (202) / result (200 with wait=true)
GET  /automation/reconcile                               running job, else the most recent one
GET  /automation/reconcile/{job_id}                      one job (no reply text, no secrets)
```

All three need `Authorization: Bearer <RECONCILIATION_SECRET>` (or
`X-Reconcile-Secret: <secret>`): `503` while no secret (16+ chars) is
configured, `401` without one, `403` for a wrong one (constant-time
comparison; the secret is never logged). `GET /automation/status` also
shows `reconciliation_*` settings and a `last_reconciliation` summary
(counts and timestamps only).

What one run does:

1. rejected (`409`) when `RECONCILIATION_ENABLED=false`,
   `AUTO_REPLY_ENABLED=false`, another reconciliation is running (the
   response names its `job_id`), or a bulk backfill is running (it already
   covers every pending review); `403` for a location outside
   `AUTO_REPLY_LOCATION_IDS`;
2. lists the location's reviews from Google and keeps the **unanswered**
   ones created/updated within `RECONCILIATION_LOOKBACK_MINUTES`, newest
   first;
3. skips reviews another run is processing right now or that recently
   ended as `FAILED_VALIDATION` / `DRY_RUN` / exhausted retries;
4. processes at most `RECONCILIATION_MAX_REVIEWS`, one at a time, each with
   `process_new_review(trigger="reconcile")` — dry run, allowlist, one
   regeneration, retry budget, per-review lock and both Google checks all
   apply. A failure is recorded and the job continues; the rest
   (`deferred`) is handled by the next call.

**Scheduling** (pick one; every 10 minutes is plenty):

- **Google Cloud Scheduler** (same project as Pub/Sub; free tier covers it):
  ```bash
  gcloud services enable cloudscheduler.googleapis.com --project=PROJECT_ID
  gcloud scheduler jobs create http gbp-reviews-reconcile --project=PROJECT_ID \
    --location=us-central1 --schedule="*/10 * * * *" --http-method=POST \
    --uri="https://YOUR-RENDER-BACKEND-DOMAIN/automation/reconcile?trigger=cloud_scheduler" \
    --headers="X-Reconcile-Secret=YOUR_RECONCILIATION_SECRET" \
    --attempt-deadline=60s
  gcloud scheduler jobs run gbp-reviews-reconcile --project=PROJECT_ID --location=us-central1  # test now
  ```
- **Render Cron Job** (separate Render service, any small image with curl):
  command `curl -fsS -X POST -H "X-Reconcile-Secret: $RECONCILIATION_SECRET" "https://YOUR-RENDER-BACKEND-DOMAIN/automation/reconcile?trigger=render_cron"`,
  schedule `*/10 * * * *`, and `RECONCILIATION_SECRET` set on the cron service.
- **GitHub Actions** (`on: schedule: - cron: "*/10 * * * *"`) or
  **cron-job.org**: the same `curl` with the secret stored as a repository
  / job secret. GitHub's schedule can lag by several minutes, which is fine
  for a safety net.

A scheduled call also wakes a sleeping free-plan instance, after which any
pending Pub/Sub retries succeed too. Treat `409` ("already running") as
success in the scheduler. Logs: `RECONCILE_STARTED job=… trigger=…
unanswered_recent=… selected=… in_progress=… recently_settled=…
deferred=…`, one `RECONCILE job=… review=… run=… outcome=…` per review,
and `RECONCILE_COMPLETED job=… published=… would_publish=… failed=…`.

### 13.12 Notification parsing, fallback resolution, and tracing a review

**Accepted notification shapes.** Field names (case-insensitive):
`type` / `notificationType` / `notification_type`, `review` / `reviewName` /
`review_name`, `location` / `locationName` / `location_name`; the type may
also come from the Pub/Sub message attributes, and an unwrapped
(`--push-no-wrapper`) body is accepted. Values are trimmed, URL-decoded (up
to twice), stripped of an API URL prefix (`https://mybusiness.googleapis.com/v4/…`)
and of leading/trailing slashes; a review may also be an object with a
`name`. Review references may be
`accounts/{a}/locations/{l}/reviews/{r}`, `locations/{l}/reviews/{r}`,
`reviews/{r}` or a bare `{r}` (the last two need a valid `location`).
Every id segment must be a plain id (`A-Z a-z 0-9 - _ . ~ = +`, no empty or
`..` segments), so traversal or junk never reaches a Google URL. A location
or account that contradicts the review resource name is rejected (acknowledged, `REJECTED_INVALID_MESSAGE`).

**Fallback resolution.** When a NEW_REVIEW has a valid location but its
review reference is missing or unparseable, the webhook does not fail. It lists the location's reviews,
keeps unanswered ones inside `RECONCILIATION_LOOKBACK_MINUTES`, orders them
deterministically (creation time, update time, review id — newest first),
skips reviews in progress or recently settled, and processes **only the
newest one** through `process_new_review()`. Nothing left →
`SKIPPED_NO_UNANSWERED_REVIEW` (200). A *well-formed* review id that Google
does not know (404) is acknowledged as `SKIPPED_NOT_FOUND` and never falls
back — so a synthetic test push can never trigger a reply to a different
real review; if a real review was behind it, the reconciliation answers it.
Several new reviews with unusable
notifications are handled one per notification (each picks the next
newest), and the reconciliation catches any remainder.

**What happened to review X?** Search the Render logs for these lines (all
carry ids; none carry tokens, secrets or headers):

```
WEBHOOK_RECEIVED message_id=… delivery_attempt=… event=NEW_REVIEW location_raw=… review_raw=…
                 normalized_location=… normalized_review=… review_id=… stage=PARSED|FALLBACK_RESOLUTION
automation run=… review=… location=… trigger=webhook status=FALLBACK_RESOLUTION reason=…   (fallback only)
automation message=… FALLBACK_RESOLUTION chose review=X created=… (newest of N free candidates)
automation run=… review=X … status=RECEIVED|FETCHING_REVIEW|PROCESSING google_reply=none|
                 GENERATING|GENERATED provider=groq|VALIDATING|PASSED|FINAL_CHECK|PUBLISHING|PUBLISHED verified=True
automation_run {"run_id": …, "review_id": "X", "trigger": …, "message_id": …, "resolution": …, "final_status": …}
WEBHOOK_RESULT message_id=… review_id=X run_id=… resolution=… final_status=… http_status=… error_type=…
RECONCILE job=… review=X run=… outcome=… final_status=…                               (reconciliation)
```

`grep "review=X"` (or the `run_id`) shows every attempt from every trigger.
A rejected message logs `WEBHOOK_REJECTED message_id=… stage=PARSING
error_type=malformed Pub/Sub push rejected: <reason with sanitized raw values>`.


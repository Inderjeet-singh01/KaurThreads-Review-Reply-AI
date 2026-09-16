# Google Review Reply AI — Phase 1

A FastAPI application for a **boutique business** that:

1. Authenticates the owner with **Google OAuth 2.0** (Business Profile management scope).
2. Finds the boutique's Business Profile location through the Google Business Profile APIs.
3. Fetches Google reviews and returns **only the ones without a business reply**.
4. Generates a professional, boutique-appropriate reply with the **Groq API**.
5. Lets the user **edit** the reply and **publish it only after explicit approval** — with a **final reply check on Google** right before publishing.

> **Safety rule:** the application may *generate* a reply automatically, but it can
> **never publish automatically**. Publishing happens only when the user explicitly
> calls the publish endpoint with the final approved text, and only if the review
> still has no business reply on Google at that moment.

---

## 1. Architecture overview

```
Google Business Profile
        │
        ▼
app/auth/google_oauth.py        (OAuth 2.0: authorize URL, callback, token file)
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
app/ai/groq_client.py           (boutique-specific reply generation)
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
│   ├── config.py                # env-based settings, token file path
│   ├── api/
│   │   └── reviews.py           # GET /reviews, POST .../generate, POST .../publish
│   ├── auth/
│   │   └── google_oauth.py      # OAuth 2.0 only (authorize, callback, token file) + auth endpoints
│   ├── google/
│   │   ├── client.py            # authenticated Business Profile REST client
│   │   └── reviews.py           # review fetching/filtering/final check/publish
│   ├── ai/
│   │   └── groq_client.py       # Groq integration (generate a reply, nothing else)
│   └── schemas/
│       └── review.py            # Pydantic API contracts
├── credentials/                 # gitignored; google_token.json created after first OAuth
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
| `GROQ_API_KEY` | yes | Groq API key from <https://console.groq.com/keys>. |
| `GOOGLE_LOCATION_ID` | no | Pin a specific Business Profile location (bare location ID or `accounts/{account}/locations/{location}`). Default: first location of the first account. |
| `GROQ_MODEL` | no | Groq model for generation. Default `llama-3.3-70b-versatile`. |

The Google OAuth token is **not** an environment variable. After the first
successful OAuth flow it is written to `credentials/google_token.json`
(gitignored) and refreshed automatically while valid.

## 5. Groq API setup

1. Create a Groq account and an API key at <https://console.groq.com/keys>.
2. Put the key in `GROQ_API_KEY` in `.env`.
3. (Optional) change `GROQ_MODEL` — any chat model available to your key works
   (default: `llama-3.3-70b-versatile`).

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
   exchanges the code for a token (with refresh token) and saves
   `credentials/google_token.json`. The callback page shows the result.
4. `GET /auth/google/status` now shows `authenticated: true`.

Notes:

- The redirect must land on `http://localhost:8000` in a browser that can reach
  the server, so run this flow on the machine where the app runs.
- If you switch Google accounts later, delete `credentials/google_token.json`
  and repeat the flow (and restart the server).

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

- **No automatic publishing.** There is no code path from review → Groq →
  Google. Only an explicit `POST /reviews/{review_id}/publish` publishes, and
  only with user-supplied final text.
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

## 11. Security notes

- All secrets live in `.env` (gitignored) — never in code or Git.
- `.gitignore` excludes `.env`, `credentials/`, virtualenvs, and bytecode.
- The OAuth token file is written `0600` and is gitignored; it is never
  returned by any endpoint (auth status returns only booleans and expiry).
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

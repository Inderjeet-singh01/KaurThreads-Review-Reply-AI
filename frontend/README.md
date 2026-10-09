# Review Reply AI — Frontend

A responsive React + TypeScript SaaS UI for the Google Business Profile
Review Reply AI backend (FastAPI). Connect a Google account, pick a business
location, review unanswered Google reviews, generate/regenerate/edit AI
replies, and post them to Google.

## Stack

- **React 18 + TypeScript + Vite**
- **Tailwind CSS** for styling (dark-navy sidebar, blue primary, SaaS look)
- **React Router** for navigation
- **lucide-react** icons
- React Context for state (auth, selected business, reviews, toasts) — no heavy
  state library.

## Prerequisites

The backend must be running (default `http://localhost:8000`) and the Google
OAuth + Groq env vars configured in the project root `.env`. See the root
`README.md`.

## Setup & run

```bash
cd frontend
cp .env.example .env      # set VITE_API_BASE_URL if the backend isn't on :8000
npm install
npm run dev               # http://localhost:5173
```

Then start the backend from the project root:

```bash
uvicorn app.main:app --reload   # http://localhost:8000
```

## Scripts

| Command           | Purpose                          |
| ----------------- | -------------------------------- |
| `npm run dev`     | Start the dev server (port 5173) |
| `npm run build`   | Type-check + production build     |
| `npm run preview` | Preview the production build      |
| `npm run lint`    | ESLint                           |

## Environment

Only one variable, and it is **not** a secret:

```
VITE_API_BASE_URL=http://localhost:8000
```

No Google/Groq secrets or tokens ever live in the frontend — all Google calls go
through the backend, which owns the OAuth token.

## Architecture

```
src/
  lib/          api.ts (centralized service + error normalization), types,
                reviewStats.ts (all metrics/trends, derived from real reviews),
                replyPreferences.ts, googleAuth.ts, authCheck.ts, utils
  context/      ToastContext, BusinessContext, ReviewsContext, SessionContext
  components/   AppShell, Sidebar, TopBar (mobile), PageHeader, BusinessSwitcher,
                MetricCard, ReviewListItem, ReplyWorkspace (reply composer),
                BulkReplyPanel, RatingDistribution, TrendChart, Modal,
                ConfirmDialog, FormControls, StatusBadge, EmptyState,
                ErrorState, Skeletons
  pages/        LoginPage, SelectBusinessPage, OverviewPage (/dashboard),
                ReviewInboxPage (/reviews and /reviews/:reviewId),
                RepliedReviewsPage, AnalyticsPage, AutomationPage, SettingsPage
  App.tsx       routing + auth/business guards
```

Design tokens (indigo `brand`, `navy` sidebar, `ink`/`canvas`/`line`) live in
`tailwind.config.js`; shared component classes (`.btn-*`, `.card`, `.input`,
`.select`) in `src/index.css`.

All network access is funneled through `src/lib/api.ts`.

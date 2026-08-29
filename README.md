# SaaS Starter Kit

A production-lean scaffold for a subscription SaaS product: a **FastAPI**
backend (Python) and a **React** single-page frontend (Vite/TypeScript), wired
up with authentication, a billing hook, a database layer, tests, and CI.

This is a starting point meant to be sold or licensed as a boilerplate —
it deliberately ships only what a real SaaS needs on day one, so the buyer
isn't paying for dead weight.

## Stack

| Layer     | Tech                                                   |
|-----------|---------------------------------------------------------|
| Backend   | Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Postgres |
| Frontend  | Node.js 20+, React 19 + Vite, React Router, TypeScript, Tailwind |
| Auth      | JWT access tokens, revocable refresh sessions, bcrypt hashing, API keys |
| Audit     | Append-only security trail, owner-readable and admin-searchable |
| Plans     | Code-defined entitlements per plan, enforced as quotas at the write paths |
| Billing   | Stripe webhook + subscription model (stubbed, wired up)  |
| Infra     | Docker Compose (Postgres + backend + nginx-served frontend) |
| CI        | GitHub Actions (lint + test, backend and frontend)        |

## Project layout

```
.
├── backend/            FastAPI service
│   ├── app/
│   │   ├── api/v1/      versioned routes (health, auth, users, api keys, sessions, audit, billing, plan)
│   │   ├── core/        settings + security (hashing, JWT) + the plan catalog
│   │   ├── models/      SQLAlchemy models
│   │   ├── schemas/     Pydantic request/response schemas
│   │   ├── services/    business logic (auth, billing, entitlements, analytics, audit, alerts)
│   │   └── scripts/     operational CLIs (Stripe sync, retention pruning)
│   ├── migrations/      Alembic migrations
│   └── tests/           pytest suite
├── frontend/           React SPA (Vite)
│   ├── index.html       Vite entry document
│   ├── nginx.conf       SPA fallback + cache headers for the production image
│   └── src/
│       ├── main.tsx      mounts React and BrowserRouter
│       ├── App.tsx       route table
│       ├── pages/        Landing, Login, Register, Dashboard, API keys, Sessions, Security
│       ├── components/   UI primitives + feature components + ProtectedRoute
│       ├── lib/          API client, auth/analytics helpers, types
│       ├── hooks/        client hooks (useAuth)
│       └── styles/       Tailwind entry + design tokens
├── docs/               deployment guide
└── .github/            CI workflows + Dependabot config
```

## Getting started

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker + Docker Compose (for Postgres, or run everything containerized)

### Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:3000

### Manual setup

**Backend**

Requires Python 3.11+ (the code uses `StrEnum` and PEP 604 unions at runtime).
If your system Python is older, [uv](https://docs.astral.sh/uv/) can fetch a
standalone interpreter without touching it:

```bash
cd backend
uv venv --python 3.12 .venv        # or: python -m venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"         # or: pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

## Testing

```bash
make test         # runs both backend (pytest) and frontend (vitest) suites
make test-backend
make test-frontend
```

## Configuration

All configuration is via environment variables — see `.env.example` for the
full list (database URL, JWT secret, Stripe keys, CORS origins).

Frontend variables must be prefixed `VITE_` to reach the browser bundle, and
Vite **inlines them at build time** — changing `VITE_API_BASE_URL` means
rebuilding the frontend image, not just restarting the container. Never put a
secret behind a `VITE_` prefix; anything with that prefix ships to the client.

Because that inlining happens at build time, every `VITE_` var also has to be
declared as an `ARG` in `frontend/Dockerfile` and forwarded from
`docker-compose.yml`. A var that only exists in `.env` will reach `npm run dev`
but is silently missing from a container build — the build still succeeds, the
value is just `undefined` at runtime.

### Secrets management

- Real secrets live only in `.env` (git-ignored) or your deploy platform's
  secret store — never in tracked files. `.env.example` ships placeholder
  values only, so `cp .env.example .env` gives you a working local setup
  with nothing sensitive in it.
- `Settings` (`backend/app/core/config.py`) refuses to start when
  `ENVIRONMENT=production` and `SECRET_KEY`, `STRIPE_SECRET_KEY`,
  `STRIPE_WEBHOOK_SECRET`, or `STRIPE_PRICE_ID_MONTHLY` are still set to
  their `.env.example` placeholder, `SECRET_KEY` is under 32 characters, or
  `DEBUG` is left on — a deploy with unset or copy-pasted-placeholder
  secrets fails fast at boot instead of running with a forgeable JWT
  signing key or a wide-open webhook endpoint.
- CI runs [gitleaks](https://github.com/gitleaks/gitleaks) on every push and
  PR (`.github/workflows/secret-scan.yml`) to catch any secret committed by
  mistake; `.gitleaks.toml` allowlists only the known-safe placeholders in
  `.env.example`.

### Error tracking

Both services report uncaught exceptions to [Sentry](https://sentry.io) via
`sentry-sdk` (backend) and `@sentry/react` (frontend) — set `SENTRY_DSN` and
`VITE_SENTRY_DSN` to enable. Leaving either unset (the default) is a
no-op: the app boots and runs the same way with no Sentry project required.

- Backend: `backend/app/core/error_tracking.py`, wired in on startup in
  `backend/app/main.py`. `SENTRY_TRACES_SAMPLE_RATE` controls what fraction
  of requests get performance traces (default `0`); errors are always
  captured regardless.
- Frontend: `frontend/src/lib/error-tracking.ts`, initialized in
  `frontend/src/main.tsx` before the first render, with
  `frontend/src/components/ErrorBoundary.tsx` mounted around the router to
  report render errors that escape the route tree. `VITE_SENTRY_ENVIRONMENT`
  overrides the environment tag, which otherwise follows the Vite build mode.

### Usage analytics

Product events are recorded in the application's own database
(`usage_events`) rather than shipped to a third-party analytics SDK, so they
can be joined against the rows they describe and are erased with the account
when it is deleted.

- The backend records the lifecycle itself — signup, verification, login,
  checkout, subscription activation and churn — so the funnel stays
  measurable even when the SPA never loads.
- The SPA records its own events through `trackEvent()`
  (`frontend/src/lib/analytics.ts`), which buffers and flushes them as one
  batch. Names must match `namespace.action_in_snake_case`; the API rejects
  anything else, which is what keeps the event-name column low cardinality.
- `GET /api/v1/analytics/me` answers for the calling account and backs the
  dashboard's activity panel. `GET /api/v1/analytics/summary` and
  `GET /api/v1/analytics/events` aggregate every account and are admin-only.
- `ANALYTICS_ENABLED=false` stops recording without affecting the read
  endpoints, which keep answering from what is already stored.

The table is append-only, so schedule the prune:

```bash
make prune                     # every retention job
make prune-usage-events        # honours ANALYTICS_RETENTION_DAYS
make prune-api-keys            # honours API_KEY_RETENTION_DAYS
make prune-sessions            # honours SESSION_RETENTION_DAYS
make prune-audit-logs          # honours AUDIT_RETENTION_DAYS
```

### Plans and quotas

What a plan *costs* lives in `backend/app/core/billing_plans.py` and syncs
to Stripe. What it *buys* lives next to it in
`backend/app/core/entitlements.py` and never leaves this repository — a
ceiling on API keys is not a concept Stripe has, and putting it in product
metadata would mean answering "how many keys may this account hold?" with a
network call to a third party during an otherwise entirely local request.

|                      | Free   | Pro       |
|----------------------|--------|-----------|
| Active API keys      | 2      | 20        |
| Signed-in devices    | 3      | 10        |
| Events per month     | 10,000 | 1,000,000 |
| Security history     | 30 days| 180 days  |

The plan is **derived from the subscription's status**, never stored on the
user. `active` and `trialing` grant the paid plan; everything else —
including `past_due` — falls back to Free, so an indefinite dunning cycle
does not keep serving paid ceilings. A cancellation takes effect when the
webhook lands, not when someone remembers to run an update.

```bash
# What am I on, and how much of it is left?
curl https://api.example.com/api/v1/plan -H "Authorization: Bearer $ACCESS_TOKEN"

# Everything on offer, cheapest first, with the caller's plan marked.
curl https://api.example.com/api/v1/plan/catalog -H "Authorization: Bearer $ACCESS_TOKEN"
```

Each ceiling is enforced where it is spent, and each refusal says something
different because the situations differ:

- **API keys** — a request over the ceiling is a `409` naming the plan and
  the number, so the reader knows whether to revoke or to upgrade. Revoked
  keys stop counting while staying listed, which makes the limit something
  an account can manage rather than a one-way door.
- **Signed-in devices** — reaching the cap retires the oldest session
  rather than refusing the login. A downgrade must not be able to lock
  somebody out of an account they hold the password to.
- **Events per month** — a batch that overshoots is *clipped*, not refused:
  the ingest response reports `recorded`, `dropped` and `remaining`, and the
  SPA stops flushing until the allowance resets. Only a batch with no room
  at all gets a `429`, with a `Retry-After` pointing at the first of the
  next month.
- **Security history** — bounds reading, not recording. Rows are kept for
  `AUDIT_RETENTION_DAYS` regardless of who is paying, so upgrading reveals
  history that already exists rather than starting a clock.

The dashboard renders all of this from `GET /plan`, with a meter per quota
that warns before a ceiling is reached rather than only after. Every limit
is nullable on the wire, and `null` means *no ceiling* rather than unknown —
so a plan added later can be unlimited without the interface needing to
recognise a sentinel number.

Two service constants — `MAX_ACTIVE_KEYS_PER_USER` and
`MAX_ACTIVE_SESSIONS_PER_USER` — remain as backstops that a plan is clamped
to. A plan is a promise about the product; those are what the deployment can
carry, and `tests/test_entitlements.py` fails if a plan ever promises more
than one of them will give.

### Sign-in sessions

Access tokens are stateless and short-lived. Refresh tokens are neither:
they last `REFRESH_TOKEN_EXPIRE_DAYS` and are what actually keeps someone
signed in, so each one gets a row in `auth_sessions` and can be stopped.

```bash
# The devices signed in to this account.
curl https://api.example.com/api/v1/sessions -H "Authorization: Bearer $ACCESS_TOKEN"

# End one of them.
curl -X DELETE https://api.example.com/api/v1/sessions/$SESSION_ID \
  -H "Authorization: Bearer $ACCESS_TOKEN"

# End all of them, sparing this one.
curl -X POST https://api.example.com/api/v1/sessions/revoke-all \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d "{\"keep_current_refresh_token\": \"$REFRESH_TOKEN\"}"
```

- **Refreshing rotates.** `POST /api/v1/auth/refresh` returns a *new*
  refresh token and retires the one presented, so a token captured in a
  proxy log stops working as soon as the real client next refreshes. A
  client that ignores the returned token will be signed out at its following
  refresh.
- **Revocation is immediate**, and leaves a tombstone, so "when did this
  device stop working?" stays answerable. `make prune-sessions` clears
  tombstones past `SESSION_RETENTION_DAYS`.
- **Resetting a password ends every session**, which is the point of
  resetting it. Signing in with the new password works right away.
- **`POST /api/v1/auth/logout`** takes the refresh token rather than an
  access token: it is the credential being revoked, and it is what a client
  still holds once its access token has lapsed. It always reports success,
  so it cannot be used to test captured tokens for validity.
- Session management needs a session JWT. Like key management, a leaked API
  key cannot use it — otherwise a stolen key could sign the owner out of
  every device and keep the account.
- Only the SHA-256 digest of each refresh token is stored, so a leaked
  database yields digests and no way to use them.

### Security audit trail

Every security-relevant thing that happens to an account gets a row in
`audit_logs`: sign-ins and refused sign-ins, keys and devices coming and
going, roles granted, accounts switched off. The account's owner reads their
own trail at `/dashboard/security`; an administrator searches across
accounts.

```bash
# My own history, newest first.
curl "https://api.example.com/api/v1/audit/me?limit=25" \
  -H "Authorization: Bearer $ACCESS_TOKEN"

# Every refused sign-in in the last day (admin).
curl "https://api.example.com/api/v1/audit?outcome=failure&action=auth.login_failed&days=1" \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN"

# One screenful of security activity (admin).
curl "https://api.example.com/api/v1/audit/summary?days=7" \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN"
```

- **Distinct from usage analytics on purpose.** `usage_events` records
  successes, for the team, and `ANALYTICS_ENABLED` can switch it off.
  `audit_logs` records the refusals too, for the account holder and for
  whoever is reconstructing an incident, and no analytics kill-switch
  disables it.
- **Failed sign-ins are attributed when the address is one we hold**, so
  "three failed attempts from an IP I don't recognise" appears in the
  owner's own trail. An attempt against an address nobody registered leaves
  an unattributed row — the address itself is never stored, so the table
  cannot become an inventory of every string typed into an email box, nor an
  enumeration oracle for whoever can read it.
- **The owner's view never names the administrator** who acted on them, only
  that somebody did. The admin view names both parties, because correlating
  one person's actions across accounts is what incident reconstruction is.
- **Reading a trail needs a session JWT.** Like session and key management, a
  leaked API key cannot: this history is a list of the addresses someone
  signs in from and a map of when they are not watching.
- A dropped write is logged and raised through the on-call alert router,
  because a gap in the trail is invisible to whoever reads it later.
- `make prune-audit-logs` clears entries past `AUDIT_RETENTION_DAYS`
  (default 180 — longer than the session window, shorter than the analytics
  one).

### API keys

Machine callers — a cron job, a CI step, a customer's integration — get a
long-lived key instead of logging in with a password and refreshing a JWT
every half hour.

```bash
# Mint one (needs a browser session; a key cannot mint another key).
curl -X POST https://api.example.com/api/v1/api-keys \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "CI deploy", "expires_in_days": 90}'

# Use it, in either header.
curl https://api.example.com/api/v1/users/me -H "X-API-Key: sk_live_…"
curl https://api.example.com/api/v1/users/me -H "Authorization: Bearer sk_live_…"
```

- Only the SHA-256 digest is stored, and the raw secret is returned exactly
  once, by the call that created it. Nothing — including the server — can
  produce it again.
- Keys are managed from `/dashboard/api-keys`, or the `/api/v1/api-keys`
  endpoints. Those routes require a session JWT: a leaked key can read and
  write the product, but cannot mint further keys or revoke the ones its
  owner would use to lock it out.
- Revocation is immediate and leaves a tombstone, so "when did this stop
  working?" stays answerable. `make prune-api-keys` clears tombstones past
  `API_KEY_RETENTION_DAYS`.
- An optional `expires_in_days` (up to 365) is what lets a key handed to a
  contractor stop working without anyone remembering to revoke it.

### Operational alerting

Alerts for the things worth waking someone for — readiness failing, a
rejected or unconfigured Stripe webhook signature, a subscription falling to
`past_due` — go through one router that applies a severity floor and then
per-key suppression, to a transport chosen by `ALERT_BACKEND`:

- `log` (the default) writes them to the structured log at a level a
  log-based monitor can watch. Works with no configuration.
- `webhook` POSTs one JSON object per alert to `ALERT_WEBHOOK_URL`, shaped
  for Slack-compatible incoming webhooks.

Suppression is the part that matters: a database outage fires the readiness
probe every few seconds, so without `ALERT_SUPPRESSION_SECONDS` the on-call
channel becomes unreadable exactly when it needs reading. Keys are
per-incident, so one customer's failed payment never masks the next one's.

Verify the routing before you need it — this sends a real notification:

```bash
curl -X POST https://api.example.com/api/v1/admin/alerts/test \
  -H "Authorization: Bearer $OWNER_ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{}'
```

### Stripe product/price catalog

Plans are defined in code (`backend/app/core/billing_plans.py`) instead of
being clicked together in the Stripe dashboard. With a real `STRIPE_SECRET_KEY`
set, sync them to that Stripe account:

```bash
make sync-stripe-catalog
```

This creates each plan's Product/Price if missing, or reuses the existing one
(matched by `lookup_key`), and prints the resulting price IDs to set as
`STRIPE_PRICE_ID_MONTHLY`. Safe to re-run.

## Deploying

[docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) covers the chosen target
(Fly.io), the secrets each service needs, migrations on release, the
scheduled retention prune, and a seven-step staging smoke test that
exercises Postgres, SMTP, Stripe in both directions and the analytics path.

## Changelog

User-visible changes are recorded in [CHANGELOG.md](./CHANGELOG.md), with an
upgrade-notes section per version covering migrations to run and settings to
add. Add an entry in the same commit as the change.

## Roadmap to PRD-ready

Everything on the 14-day plan in `ROADMAP.md` has landed except the two
items that need a real deployment to close: a staging environment smoke
tested end to end (the checklist is written — see
[docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md)) and a PRD finalized against what
actually shipped.

[docs/PRD.md](./docs/PRD.md) is the product requirements doc written
against what actually shipped — scope, explicit non-goals, and the two
items still open at ship time.

## License

See [LICENSE.md](./LICENSE.md) — proprietary placeholder, replace before
distribution.

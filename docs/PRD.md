# Product Requirements — SaaS Starter Kit

This PRD is written against what actually shipped, not what was originally
proposed. Where the two differ, the code is correct and this document
follows it — see `CHANGELOG.md` for how each feature arrived and
`ROADMAP.md` for what is still open.

## Problem and audience

Teams starting a subscription SaaS product re-solve the same handful of
problems before writing any product-specific code: auth, billing, role
gating, transactional email, and the operational plumbing (logging, error
tracking, health checks) a real deploy needs. This kit is that scaffold,
sold or licensed as a boilerplate: a buyer clones it, replaces the
`Pro`/`$29` placeholder plan and the landing/dashboard copy with their own,
and has a deployable product rather than a from-scratch build.

The buyer is a developer or small team, not an end user of the eventual
product. Success is measured by how little they have to touch before their
own feature work starts, not by any UI polish beyond that starting point.

## Scope

### In scope (shipped)

**Auth and accounts**
- Register/login/refresh with JWT access + refresh tokens, bcrypt password
  hashing (`backend/app/services/auth_service.py`,
  `backend/app/core/security.py`).
- Email addresses are case-folded at the database level (migration `0006`):
  `Ada@example.com` and `ada@example.com` are the same account.
- Email verification: single-use tokens, gated behind
  `REQUIRE_EMAIL_VERIFICATION` so local dev with the console email transport
  isn't blocked (`backend/app/services/verification_service.py`).
- Password reset end-to-end: request, single-use token, complete
  (`backend/app/services/password_reset_service.py`).
- Account deactivation (reversible, admin-only) and account deletion
  (irreversible, self-service) with data export covering the account's own
  rows including usage events (`backend/app/services/account_service.py`).
- Transactional email behind a `Protocol`
  (`backend/app/services/email/base.py`) with `console` (logs the email,
  for local dev), `memory` (for tests) and `smtp` backends, selected by
  `EMAIL_BACKEND`.

**Billing**
- Plan catalog defined in code (`backend/app/core/billing_plans.py`), synced
  to a real Stripe account with `make sync-stripe-catalog`
  (`backend/app/services/stripe_catalog.py`) — the Stripe dashboard is never
  hand-edited.
- Checkout session and customer-portal redirect flows
  (`backend/app/api/v1/endpoints/billing.py`).
- Webhook handling with signature verification and replay protection
  (`backend/app/models/webhook_event.py` records processed event IDs), and
  subscription state sync covering `active`, `past_due`, `canceled` and
  trialing.

**Access control**
- Three-level role hierarchy — `member` < `admin` < `owner`
  (`backend/app/models/role.py`) — with a rank comparison rather than
  per-permission grants, since a single-tenant SaaS doesn't need finer
  granularity on day one.
- Admin endpoints for listing/filtering accounts and toggling
  `is_active` (`backend/app/api/v1/endpoints/admin.py`); an admin cannot act
  on their own account or one at or above their own role.
- Per-IP rate limiting on the auth endpoints
  (`backend/app/core/rate_limit.py`, `backend/app/api/deps_rate_limit.py`).
- Security headers middleware and a production config guard
  (`backend/app/core/config.py`) that refuses to boot when
  `ENVIRONMENT=production` and any secret is still at its `.env.example`
  placeholder, `SECRET_KEY` is under 32 characters, or `DEBUG` is on.
- Gitleaks runs on every push/PR (`.github/workflows/secret-scan.yml`) to
  catch a committed secret before it merges.

**Observability**
- JSON structured logging with a request correlation ID threaded through
  the request lifecycle (`backend/app/core/logging.py`,
  `backend/app/core/request_context.py`,
  `backend/app/middleware/request_id.py`).
- Sentry error tracking on both services, off by default (empty DSN is a
  no-op) so no Sentry project is required to run the app locally.
- Liveness and readiness probes (`backend/app/api/v1/endpoints/health.py`)
  suitable for a platform health check.
- Usage analytics recorded in the app's own database
  (`backend/app/models/usage_event.py`,
  `backend/app/services/analytics_service.py`) rather than a third-party
  SDK, so events are erased with the account on deletion. Backend-recorded
  lifecycle events (signup, verification, login, checkout, activation,
  churn) keep the funnel measurable even if the SPA never loads; the SPA
  additionally records its own events through `trackEvent()`. Retention is
  enforced by a scheduled prune (`make prune-usage-events`), since the table
  is append-only.

**Ship prep**
- Multi-stage, non-root, slim Dockerfiles for both services.
- Deploy target decided and documented: Fly.io, with the reasoning and a
  seven-step staging smoke test in `docs/DEPLOYMENT.md`.
- Dependency updates via Dependabot, weekly and grouped, across backend,
  frontend, workflow actions and both Dockerfiles.
- `CHANGELOG.md` maintained per change, with an upgrade-notes section per
  version for anyone taking an update.

**Frontend**
- React 19 + Vite SPA (not server-rendered) — landing, login, register, and
  a protected dashboard with a billing card and an activity panel showing
  the account's own usage events.
- Served by nginx in production, built via a multi-stage Dockerfile.

### Explicitly out of scope

These were considered and deliberately left out, because they add
buyer-specific decisions this kit shouldn't make on their behalf:

- **Multi-tenancy / organizations.** The role hierarchy is per-account, not
  per-organization-membership. A buyer needing team accounts adds that
  layer themselves.
- **Additional billing plans, usage-based billing, or seats.** One plan
  (`pro_monthly`) ships as a working example of the catalog pattern, not as
  a complete pricing page.
- **A first-party analytics dashboard for admins beyond the summary/events
  endpoints already shipped.** `GET /api/v1/analytics/summary` and
  `GET /api/v1/analytics/events` exist as an API; a buyer wanting charts
  builds their own view over them.
- **Any UI design system beyond Tailwind utility classes already in use.**
  The landing/dashboard pages are a starting layout, not a finished brand.
- **A specific paging vendor's SDK** (PagerDuty, Opsgenie, etc.). Sentry
  captures and groups errors, and `app/services/alerts` gives on-call
  routing a vendor-agnostic webhook hook; wiring that to a particular
  provider's API instead of its generic webhook intake is a buyer-specific
  choice this kit shouldn't make on their behalf.

## Known gaps at ship time

One roadmap item is open, and this PRD does not claim it's done:

- **Staging smoke test.** `docs/DEPLOYMENT.md` has a seven-step smoke test
  that exercises Postgres, SMTP, Stripe in both directions, and the
  analytics path, but nobody has run it against a real deploy — there is no
  standing staging environment in this repository's CI. This stays a
  documented procedure, not a checked-off result, until someone runs it
  against an actual Fly.io deploy.

On-call/monitoring alert routing is closed: `app/services/alerts`
(console transport by default, JSON webhook when `ALERT_BACKEND=webhook`)
plus the `check_health_and_alert` script — schedulable the same way as
`prune_usage_events` — give a buyer a routing hook straight into Slack,
PagerDuty, Opsgenie or any other generic-webhook intake. See
`docs/DEPLOYMENT.md` for the env vars and the schedule to set up.

## Success criteria

A buyer can, without touching backend or frontend framework code:
1. Clone the repo, run `docker compose up --build`, and reach a working
   register → verify → login → subscribe → cancel flow locally within
   `.env.example` defaults (Stripe test mode, console email transport).
2. Replace `BILLING_PLANS` with their own catalog and get real prices in
   Stripe with `make sync-stripe-catalog`.
3. Deploy to Fly.io following `docs/DEPLOYMENT.md` with no code changes,
   only secrets and the two `fly.toml` files.
4. Update to a new release by reading `CHANGELOG.md`'s upgrade notes for
   that version, rather than diffing the whole repo.

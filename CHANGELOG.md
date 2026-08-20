# Changelog

Every change that reaches `main` and affects someone running this code is
recorded here. Licensees read this file to decide whether an update is worth
taking and what it will cost them to take it, so entries describe the change
in terms of what it means for a deployment, not which files moved.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## How this file is maintained

- Add an entry under `## [Unreleased]` in the same commit as the change
  itself. A changelog written at release time is a changelog written from
  `git log`, which is where the reasons have already been lost.
- One entry per user-visible change. Internal refactors, test additions and
  dependency bumps that change no behaviour do not need one.
- Anything requiring action from someone upgrading — a migration to run, a
  new environment variable, a changed default — goes under **Upgrade notes**
  for that version. That section is the reason this file exists.
- On release, rename `Unreleased` to the new version with the date and open
  a fresh `Unreleased` heading above it.

## [Unreleased]

### Added

- **Usage analytics.** Product events are recorded in an append-only
  `usage_events` table: signup, verification, login, checkout, subscription
  activation and churn are captured server-side, and the SPA can record its
  own events through `POST /api/v1/analytics/events`. Accounts see their own
  activity at `GET /api/v1/analytics/me`; admins see the whole account at
  `GET /api/v1/analytics/summary` and the raw feed at
  `GET /api/v1/analytics/events`.
- **Activity panel on the dashboard**, showing the signed-in account's event
  total, a sparkline of the daily series, and its most frequent events over a
  7, 14 or 30 day window.
- **Account deactivation.** `PATCH /api/v1/admin/users/{id}/status` switches
  an account off and back on — the reversible counterpart to deletion. An
  admin cannot change their own status or that of an account at or above
  their role. `GET /api/v1/admin/users` accepts an `is_active` filter.
- **Retention pruning.** `make prune-usage-events` (or
  `python -m app.scripts.prune_usage_events`) deletes events past
  `ANALYTICS_RETENTION_DAYS`. Run it on a schedule; the table is append-only
  otherwise.
- **On-call alert routing.** `app/services/alerting` posts a JSON payload to
  `ALERT_WEBHOOK_URL` when `ALERT_BACKEND=webhook` — Slack incoming webhooks
  render it directly, and PagerDuty/Opsgenie's generic webhook intake accept
  it as-is. `python -m app.scripts.check_health_and_alert` checks the
  database directly and fires an alert on failure; run it on a schedule the
  same way as `prune_usage_events`.

### Fixed

- **Email addresses are treated case-insensitively.** Registering as
  `Ada@example.com` and `ada@example.com` previously created two accounts on
  one inbox, and login only matched the exact spelling used at signup.
- **Deactivated accounts can no longer log in.** `is_active` was checked on
  every request except the one that issues tokens, so a switched-off account
  could still obtain a refresh token that outlived the deactivation by up to
  `REFRESH_TOKEN_EXPIRE_DAYS`.
- **Data export and erasure now cover usage events.** An access request
  returns them, and deleting an account removes them explicitly rather than
  relying on a foreign key that SQLite does not enforce.

### Changed

- Dependency updates arrive weekly and grouped, via Dependabot across the
  backend, frontend, workflow actions and both Dockerfiles.

### Upgrade notes

- Run `alembic upgrade head`. Migration `0005` creates `usage_events`;
  migration `0006` folds existing user emails to lowercase.
- **Migration 0006 will refuse to run** if two accounts hold addresses that
  differ only by case, and will name them. Merge or delete the duplicates,
  then re-run it. This is deliberate: folding them regardless would collide
  with the unique index and abort with an error identifying nobody.
- New optional settings, all with working defaults: `ANALYTICS_ENABLED`,
  `ANALYTICS_RETENTION_DAYS`, `ANALYTICS_MAX_BATCH_SIZE`, `ALERT_BACKEND`,
  `ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_TIMEOUT_SECONDS`.
- Login now answers `403` (not `401`) for a deactivated account. A client
  that treats every failed login identically needs no change; one that
  distinguishes them should surface the deactivation message.

## [0.1.0] - 2026-08-17

First tagged state of the starter kit.

### Added

- FastAPI backend: JWT auth (register, login, refresh) with bcrypt hashing,
  SQLAlchemy 2.0 models, Alembic migrations, and a health/liveness/readiness
  probe set.
- Stripe billing: a code-defined product/price catalog with a sync command,
  checkout and customer-portal flows, and webhook handling with signature
  verification, replay protection and subscription state sync.
- Account lifecycle: transactional email behind a transport Protocol, email
  verification, password reset, data export and account deletion.
- Access control and hardening: owner/admin/member roles, per-IP rate limits
  on the auth endpoints, security headers, and a production config guard that
  refuses to boot on placeholder secrets.
- Observability: JSON structured logging with request correlation IDs, and
  Sentry error tracking on both services.
- React/Vite SPA: landing, login, register and a protected dashboard with a
  billing card, served by nginx in production.
- Docker Compose for local development, and GitHub Actions running lint,
  typecheck, tests and a secret scan.

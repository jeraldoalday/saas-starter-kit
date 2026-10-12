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

- **Outbound webhooks.** An account can register URLs to be POSTed to when
  something happens: `GET/POST /api/v1/webhooks/endpoints`, `PATCH`/`DELETE`
  on one of them, `POST .../ping` to queue a test delivery,
  `GET /api/v1/webhooks/deliveries` for the attempt log, and
  `GET /api/v1/webhooks/event-types` (unauthenticated) for what can be
  subscribed to. Managed from `/dashboard/webhooks`. Five events ship:
  `api_key.created`, `api_key.revoked`, and the three subscription
  transitions. Every request carries an HMAC-SHA256 signature in
  `X-Webhook-Signature` over `"{timestamp}.{body}"`, with the timestamp
  inside the signed string so rewriting it cannot rescue a replay. Failed
  deliveries retry six times over about nine hours; an endpoint failing 20
  deliveries in a row is switched off with the reason recorded. Endpoint
  management requires a session JWT, so a leaked API key cannot redirect an
  account's events or read the secret that would let it forge them.
- **A webhook endpoint ceiling per plan.** One on Free, five on Pro,
  reported as a `webhook_endpoints` quota by `GET /api/v1/plan` alongside
  the existing three. An endpoint the owner has switched off does not hold
  a slot.
- **Webhook destinations are validated against SSRF.** A registered URL must
  be https, resolve entirely to public addresses, and use one of a short
  list of ports — so a customer cannot have the server POST to the cloud
  metadata service, the database, or the API itself.
  `WEBHOOK_ALLOW_INSECURE_URLS=true` relaxes it for local development.
- **`make deliver-webhooks`** sends what is queued, one bounded pass per
  invocation, and `make deliver-webhooks-loop` runs it as a worker.
  **`make prune-webhook-deliveries`** removes finished deliveries past
  `WEBHOOK_DELIVERY_RETENTION_DAYS`; pending ones are never pruned.
- **Data exports include registered webhook endpoints.** Metadata only —
  URL, description, subscriptions, and whether delivery has given up on it.
  Where an account's data has been going is exactly what a subject access
  request is for. The signing secret is excluded, like the password hash
  and the API key digests; it is readable from the endpoint that exists to
  serve it, which is audited, and a carried-away file is not.
- **`webhook_endpoints` and `pending_webhook_deliveries` on
  `/api/v1/admin/overview`.** The second is the operational one: a pending
  figure that only climbs means the delivery worker is not running, which is
  otherwise a completely silent failure.
- **`disabled_webhook_endpoints` on `/api/v1/admin/overview`.** Counts only
  endpoints the delivery worker itself switched off after 20 consecutive
  failures, not ones an owner turned off by hand — a figure that climbs
  means receivers are failing across the deployment, not that one customer
  is having a bad day.

- **API keys.** Long-lived credentials for calling the API without a browser
  session: `POST/GET /api/v1/api-keys` and `DELETE /api/v1/api-keys/{id}`,
  managed from `/dashboard/api-keys`. Keys authenticate through `X-API-Key`
  or as a bearer token, carry an optional expiry, and are stored as a
  SHA-256 digest — the raw secret is returned exactly once, by the call that
  created it. Key management itself still requires a session JWT, so a
  leaked key cannot mint more of itself.
- **Revocable sign-in sessions.** Every refresh token now has a row in
  `auth_sessions`, so a session can be ended. `GET /api/v1/sessions` lists
  the devices signed in to an account, `DELETE /api/v1/sessions/{id}` ends
  one, and `POST /api/v1/sessions/revoke-all` ends all of them — optionally
  sparing the caller's own, for "sign out my other devices". Managed from
  `/dashboard/sessions`. Session management requires a session JWT, so a
  leaked API key cannot sign the owner out and keep the account.
- **Refresh tokens rotate.** `POST /api/v1/auth/refresh` now returns a new
  refresh token and retires the one presented, so a token captured in
  transit stops working the next time the real client refreshes rather than
  lasting the rest of its lifetime.
- **A logout endpoint.** `POST /api/v1/auth/logout` revokes the session
  behind a refresh token. It takes the refresh token rather than an access
  token, because that is the credential being revoked and it is what a
  client still holds once its access token has lapsed. It always reports
  success, so it cannot be used to test captured tokens for validity.
- **Retention prune for dead sessions.** `make prune-sessions` clears
  revoked and expired session rows past `SESSION_RETENTION_DAYS`, and spent
  one-time tokens along with them. `make prune` runs all three retention
  jobs.
- **Admin overview.** `GET /api/v1/admin/overview` reports deployment-wide
  counts — accounts by state, API keys, sessions live and total,
  subscriptions by status — for answering "is anything obviously wrong"
  without paging through lists.
- **On-call alert routing.** Readiness failures, rejected or unconfigured
  Stripe webhook signatures, and subscriptions falling to `past_due` raise
  alerts through a router that applies a severity floor and per-key
  suppression, delivered to the structured log (default) or to a
  Slack-compatible webhook. `POST /api/v1/admin/alerts/test` (owner-only)
  pushes a real alert through the real path, so the configuration is
  verifiable before the incident that needs it, and
  `python -m app.scripts.check_health_and_alert` watches the database from
  outside the app — an instance that is too unhealthy to run its own
  readiness check is also too unhealthy to report it.
- **API keys appear in the data export.** A data-access request returns key
  metadata — name, prefix, created, last used, expiry, revocation — and never
  the digest, which is a credential rather than user data.
- **Retention prune for revoked API keys.** `make prune-api-keys` clears
  tombstones past `API_KEY_RETENTION_DAYS`; `make prune` runs both retention
  jobs.
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
- **Security audit trail.** Every security-relevant event now gets a row in
  `audit_logs`: sign-ins and refused sign-ins, keys and devices coming and
  going, roles granted, accounts switched off and on, data exported, account
  erased. An account reads its own history at `GET /api/v1/audit/me` and at
  `/dashboard/security`; an administrator searches across accounts at
  `GET /api/v1/audit` (filterable by action, outcome, account and window)
  and reads one screenful at `GET /api/v1/audit/summary`.

  Deliberately a separate table from `usage_events`. Analytics records
  successes for the team and `ANALYTICS_ENABLED` switches it off; the audit
  trail records the refusals too, for the account holder and for whoever is
  reconstructing an incident, and no analytics kill-switch disables it.

  A failed sign-in is attributed to the account when the address is one you
  hold, so the owner sees the attempt in their own history. The address
  itself is never stored: an attempt against an unregistered address leaves
  an unattributed row instead, so the table cannot become an inventory of
  every string typed into an email box, nor an enumeration oracle. The
  owner's view says "by another account" rather than naming the
  administrator who acted; the admin view names both parties.

  Reading a trail requires a session JWT. A leaked API key cannot, for the
  reason it cannot manage keys or sessions.
- **`make prune-audit-logs`** deletes trail past `AUDIT_RETENTION_DAYS`
  (default 180). `make prune` now runs all four retention jobs.
- **Security figures on the admin overview.** `GET /api/v1/admin/overview`
  reports `failed_logins_last_hour` — refusals across every account, so a
  spray is visible without opening the trail — and `audit_entries`, whose
  drifting far above the retention window is how you find out the scheduled
  prune stopped running.
- **Plans now grant different ceilings, and the ceilings are enforced.**
  Free accounts get 2 API keys, 3 signed-in devices, 10,000 events a month
  and 30 days of security history; a paid subscription raises those to 20,
  10, 1,000,000 and 180 days. `GET /api/v1/plan` reports the plan, every
  quota and what is left of it, and `GET /api/v1/plan/catalog` lists what is
  on offer with the caller's plan marked. Both are readable with an API key
  as well as a session, so a programmatic client can back off before it
  starts being refused. The dashboard renders them as meters that warn
  before a limit is reached rather than only after.
  The plan is derived from the subscription's status rather than stored, so
  a cancellation takes effect when the webhook lands. `active` and
  `trialing` grant the paid plan; everything else, `past_due` included,
  falls back to Free.
- **Event ingest reports what it actually stored.** `POST
  /api/v1/analytics/events` now answers with `dropped` and `remaining`
  alongside `recorded`. A batch that overshoots the monthly allowance is
  clipped rather than refused — a client flushing a fixed-size buffer would
  otherwise lose the room it still had — and only a batch with no room at
  all gets a `429`, carrying a `Retry-After` that points at the first of the
  next month. The bundled SPA honours it and stops flushing until then.
- **The owner-facing audit page says how far back it reaches.**
  `GET /api/v1/audit/me` now carries `history_days`, and bounds both the
  page and its total to that window. Rows are still kept for
  `AUDIT_RETENTION_DAYS` regardless of plan, so upgrading reveals history
  that already exists.
- **`BCRYPT_ROUNDS` configures bcrypt's cost factor**, default `12`,
  rejected below `10` under `ENVIRONMENT=production` the same way an
  insecure `SECRET_KEY` already is. Exists mainly so the test suite — which
  hashes a password in nearly every fixture — can run at a much cheaper
  setting (`make test-backend` and CI both do) without the production
  default ever moving: a bcrypt hash carries its own cost factor, so
  lowering this does not touch a password already hashed at a higher one.

### Fixed

- **The SPA now redeems its refresh token instead of signing out at
  `ACCESS_TOKEN_EXPIRE_MINUTES`.** `POST /api/v1/auth/refresh` existed and
  was fully tested on the backend, but nothing in `frontend/src` ever called
  it — every request the client made carried the original access token and
  nothing rotated it, so a signed-in tab was silently logged out the moment
  that token expired (30 minutes by default) regardless of how long its
  refresh token, and the `auth_sessions` row behind it, still had to run.
  `apiRequest` now retries a request once with a freshly redeemed access
  token when it comes back `401`, and concurrent requests that expire at the
  same moment share one redemption instead of racing the refresh token's
  single-use rotation against each other. A request that carried no token,
  or a refresh that itself fails, still ends the session exactly as before.
- **The security page names webhook management, instead of showing it raw.**
  `describeAuditAction` had no wording for the five webhook actions
  (`webhook.endpoint_created`, `.endpoint_updated`, `.endpoint_deleted`,
  `.secret_rotated`, `.secret_revealed`) added to the backend's audit trail
  with outbound webhooks — so registering an endpoint or rotating its secret
  showed up in an account's own history at `/dashboard/security` as the raw
  action string rather than a plain-English label like the rest of the page.
- **Checkout refuses an account that is already subscribed.** Stripe's
  checkout in `mode="subscription"` does not refuse a second subscription
  for the same customer — it opens one, and the account is billed twice. A
  stale tab, a resubmitted form, or a client retrying a slow response could
  all reach `POST /billing/checkout` while already `active` or `trialing`.
  It now answers `409` and points at the billing portal instead, the same
  way account deletion already refuses while a subscription is active.
- **Resetting a password now signs every device out.** A reset changed the
  password and nothing else, so anyone already holding a refresh token —
  which is the reason people reset a password — kept minting access tokens
  straight through it, for up to `REFRESH_TOKEN_EXPIRE_DAYS`. The account
  owner had no way to tell and no action that would have helped. Signing
  back in with the new password works immediately.
- **Signing out ends the session on the server.** The SPA cleared its own
  `localStorage` and stopped there; the refresh token stayed valid for
  weeks, so "Log out" ended the session in that tab and nowhere else.
  Tokens are still cleared locally first, so signing out works on a flaky
  connection.
- **Spent one-time tokens are pruned.** Email verification and password
  reset tokens were deleted only once expired, so a redeemed token sat in
  the table for the rest of its lifetime — a day per signup.
- **Batched analytics ingest no longer over-reports.** A failure partway
  through a batch rolls the whole transaction back, but the response still
  counted the rows queued before it — so a failing ingest path looked
  healthy while the events table stayed empty.
- **Stripe invoices are read in both API-version shapes.** Stripe's
  2025-03-31 version moved `invoice.subscription` under
  `invoice.parent.subscription_details` and `current_period_end` onto
  subscription items. On an account upgraded in the Stripe dashboard,
  `invoice.payment_failed` had stopped marking anyone `past_due` and period
  ends had stopped syncing, silently.
- **Rate-limit counters are pruned against their own window.** The lazy
  prune judged every counter by the window of whichever request triggered
  it, so login traffic (60s) could clear a caller's half-spent
  password-reset budget (300s) and hand them a fresh allowance.
- **Alert configuration that could never deliver is rejected at boot.** An
  unknown `ALERT_BACKEND` fell through to the log transport and an unknown
  `ALERT_MIN_SEVERITY` outranked every real severity, so either typo paged
  nobody while reading as configured.
- **A rejected session token is dropped by the SPA.** A 401 left the token
  in `localStorage`, so every protected route repeated the same doomed
  request and the analytics buffer kept queueing events that could never be
  sent. Cleared on an explicit 401 only — a network failure is not a logout.
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
- **Data export and erasure cover the audit trail too.** An access request
  returns the account's own entries — its security history is the part
  people most often ask for — but never the entries where it was the actor,
  since those describe somebody else's account. Erasure deletes the rows
  where the account is the subject and disowns the ones where it was the
  actor, so deleting an administrator does not erase the record of what
  they did to other people's accounts.
- **The admin overview no longer loads every session row.** It counted
  active sessions by fetching the whole of `auth_sessions` and summing a
  property in Python — on the one endpoint whose cost scales with the entire
  deployment. It is a `COUNT` with a `WHERE` clause now, like every other
  figure on that response.
- **Data exports were missing every signed-in device.** `auth_sessions`
  arrived after `GET /api/v1/users/me/export` was written and no branch was
  ever added for it, so a subject access request came back without the one
  table recording an IP address and a User-Agent per device. Metadata only —
  the token digest is a credential and stays out, like the password hash.
  Deletion was already correct; this was the read side.
- **Retention jobs deleted the wrong rows when handed a non-UTC clock.**
  Every prune helper takes an optional `now`, and bound it straight into a
  SQL comparison. SQLite drops tzinfo when binding, so an aware datetime in
  another zone was compared as though its local reading were UTC — off by
  the zone's offset either side of where the window falls. Deployments whose
  scheduled job passes no clock at all were never affected.
- **A password that clears the character limit but not the byte one no
  longer 500s.** The schema caps a password at 72 characters; bcrypt's real
  limit is 72 *bytes*, and a multi-byte password (72 accented letters, or
  fewer emoji) can clear the first while failing the second. `/register` and
  `/reset-password` now answer `400` with a message naming the byte limit,
  instead of the request reaching `hash_password()` and raising unhandled.

### Changed

- Dependency updates arrive weekly and grouped, via Dependabot across the
  backend, frontend, workflow actions and both Dockerfiles.

### Upgrade notes

- **Schedule the webhook delivery worker, or nothing is delivered.** The API
  queues deliveries; `python -m app.scripts.deliver_webhooks` sends them.
  Without it, endpoints register successfully, events queue successfully,
  and no customer's handler ever fires — every request involved returns
  2xx, so there is no error anywhere to notice. Run exactly one worker: the
  queue has no locking, so two would send everything twice. See
  `docs/DEPLOYMENT.md#6-webhook-delivery-worker`.
- **Add the webhook prune to the retention job.**
  `python -m app.scripts.prune_webhook_deliveries`, alongside the other
  four. `webhook_deliveries` grows with traffic rather than with the number
  of accounts, so it is the fastest-growing table this ships.
- **New optional settings, all with working defaults:** `WEBHOOKS_ENABLED`,
  `WEBHOOK_DELIVERY_TIMEOUT_SECONDS`, `WEBHOOK_DELIVERY_BATCH_SIZE`,
  `WEBHOOK_DELIVERY_RETENTION_DAYS`, `WEBHOOK_ALLOW_INSECURE_URLS`. Leave
  the last one `false` in production — `true` re-opens the SSRF surface the
  destination check exists to close.
- **Free accounts get one webhook endpoint.** Nothing existing is affected,
  since nothing could register one before this release.

- **Free accounts are now capped where they were not before.** An existing
  account with no live subscription is on the Free plan, so it may hold 2
  API keys rather than 20 and keep 3 devices signed in rather than 10. Keys
  already minted keep working — nothing is revoked — but creating another
  past the ceiling is refused until one is revoked or the account
  subscribes. A `past_due` subscription counts as Free, which is a change
  for any deployment carrying accounts in dunning.
- **Clients that read the ingest response should read `dropped`.** A `202`
  no longer means the whole batch was stored. Ignoring the field is safe but
  silently lossy once an account reaches its monthly allowance.
- No new environment variables. Plan ceilings are code, in
  `backend/app/core/entitlements.py` — change them there and the tests will
  tell you if a plan promises more than the service backstops will give.
- Run `alembic upgrade head`. Migration `0005` creates `usage_events`;
  migration `0006` folds existing user emails to lowercase; migration `0007`
  creates `api_keys`; migration `0008` creates `auth_sessions`; migration
  `0009` creates `audit_logs`; migration `0010` creates `webhook_endpoints`
  and `webhook_deliveries`.
- **Install the audit prune alongside the others.** `audit_logs` is
  append-only, so add `python -m app.scripts.prune_audit_logs` to the
  scheduled retention job (see `docs/DEPLOYMENT.md`). Its window is
  `AUDIT_RETENTION_DAYS`, separate from the session one on purpose: how long
  a security trail is kept is a different policy decision from how long a
  dead session row is.
- The trail starts empty. Nothing before this upgrade is recorded, and there
  is nothing to backfill from — `usage_events` never held the refusals, which
  is most of what the trail is for.
- **Everyone is signed out by this upgrade.** Refresh tokens issued before
  migration `0008` have no session row, and `POST /api/v1/auth/refresh` now
  requires one, so outstanding refresh tokens stop working and users log in
  again once. Access tokens already in flight keep working until they
  expire. There is no way to backfill rows for tokens that were never
  recorded, and the alternative — honouring any correctly signed token —
  would leave exactly the unrevocable tokens this release exists to remove.
- Clients that store the refresh token must save the new one returned by
  `POST /api/v1/auth/refresh`. Refresh previously echoed the same token
  back, so a client that ignores the field kept working; it will now be
  signed out at the following refresh. The bundled SPA already stores it.
- **Migration 0006 will refuse to run** if two accounts hold addresses that
  differ only by case, and will name them. Merge or delete the duplicates,
  then re-run it. This is deliberate: folding them regardless would collide
  with the unique index and abort with an error identifying nobody.
- New optional settings, all with working defaults: `ANALYTICS_ENABLED`,
  `ANALYTICS_RETENTION_DAYS`, `ANALYTICS_MAX_BATCH_SIZE`,
  `API_KEY_RETENTION_DAYS`, `SESSION_RETENTION_DAYS`, `ALERT_BACKEND`,
  `ALERT_WEBHOOK_URL`,
  `ALERT_MIN_SEVERITY`, `ALERT_SUPPRESSION_SECONDS`,
  `ALERT_WEBHOOK_TIMEOUT_SECONDS`. `ALERT_BACKEND=console` is accepted as a
  synonym for `log`.
- **The app now refuses to boot on invalid alert settings** — an
  unrecognised `ALERT_BACKEND` or `ALERT_MIN_SEVERITY`, or `ALERT_BACKEND=webhook`
  with no `ALERT_WEBHOOK_URL`. Deployments that set none of them are
  unaffected: the defaults are valid.
- Schedule `make prune-api-keys` and `make prune-sessions` alongside the
  existing usage-event prune, or replace all three with `make prune`.
- The `User` object returned by `GET /api/v1/users/me` is unchanged, but the
  SPA's `User` type now includes `role`, which the API has been returning
  since RBAC landed. Client code that constructs a `User` literal (tests,
  fixtures) needs the field added.
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

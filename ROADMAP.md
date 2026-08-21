# Roadmap: scaffold → PRD-ready

Tracking doc for the 14-day push from this scaffold to a sellable,
PRD-ready product. Update as items land; this is a working checklist, not
a spec.

<!--
"Staging environment smoke-tested end-to-end" is the next unchecked item
and is currently blocked, not skipped: it requires an actual staging
deploy (Fly.io apps for backend/frontend, a managed Postgres, live Stripe
test-mode keys with a real webhook endpoint, and a working SMTP relay) to
run the checklist in docs/DEPLOYMENT.md#smoke-test against. None of that
infrastructure exists or is reachable from this automated environment, and
there are no deploy/Stripe/SMTP credentials configured here. Faking a run
against nonexistent infra would misrepresent the state of the product, so
this stays unchecked until someone with access to a real staging
deployment runs the seven-step checklist and reports the result. Remove
this note once that happens and the box is checked.
-->

## Day 1-2 — Foundation (this scaffold)
- [x] Backend skeleton (FastAPI, config, health check)
- [x] Database layer (SQLAlchemy models, Alembic migrations)
- [x] Auth (register/login/refresh, JWT, bcrypt)
- [x] Billing stub (subscription model, Stripe webhook handler)
- [x] Backend test suite + CI
- [x] Frontend skeleton (Next.js, Tailwind, layout)
- [x] Auth pages + API client + dashboard shell
- [x] Frontend test suite + CI
- [x] Docker Compose for local dev

## Day 3-5 — Real billing
- [x] Stripe product/price catalog wired to real Stripe account
- [x] Checkout session + customer portal flow
- [x] Webhook signature verification against live secret
- [x] Subscription state sync (past_due, canceled, trial)

## Day 6-8 — Account lifecycle
- [x] Transactional email (verification, password reset) via provider
- [x] Email verification gate on protected routes
- [x] Password reset flow end-to-end
- [x] Account deletion / data export (GDPR-adjacent basics)

## Day 9-10 — Access control & hardening
- [x] Role-based access control (owner/admin/member)
- [x] Rate limiting on auth endpoints
- [x] Input validation audit + security headers
- [x] Secrets management review (no plaintext secrets in repo/CI)

## Day 11-12 — Observability & ops
- [x] Structured logging (backend + frontend)
- [x] Error tracking (Sentry or equivalent)
- [x] Health/readiness probes for orchestration
- [x] Basic usage metrics/analytics hook

## Day 13-14 — Ship prep
- [x] Production Dockerfiles (multi-stage, non-root, slim images)
- [x] Deploy target decided + documented (Fly.io — see docs/DEPLOYMENT.md)
- [ ] Staging environment smoke-tested end-to-end (checklist written; needs a real deploy to run against)
- [x] PRD finalized against what actually shipped (docs/PRD.md)

## Beyond the plan — shipped since

- [x] API keys for programmatic access (hashed at rest, expiring, revocable)
- [x] Migrations exercised against a real Postgres in CI, both directions
- [x] Revocable sign-in sessions: refresh-token rotation, a logout endpoint,
      "sign out my other devices", and password reset ending every session
- [x] Admin overview endpoint (accounts, credentials, subscriptions at a glance)

## Post-PRD — Maintenance mode
- [x] Dependency update cadence (Dependabot, weekly + grouped)
- [x] On-call/monitoring alert routing (severity floor + suppression; log or webhook transport, plus an out-of-process watcher)
- [x] Changelog process for buyers/licensees (CHANGELOG.md)

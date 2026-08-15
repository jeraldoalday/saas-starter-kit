# Roadmap: scaffold → PRD-ready

Tracking doc for the 14-day push from this scaffold to a sellable,
PRD-ready product. Update as items land; this is a working checklist, not
a spec.

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
- [ ] Webhook signature verification against live secret
- [ ] Subscription state sync (past_due, canceled, trial)

## Day 6-8 — Account lifecycle
- [ ] Transactional email (verification, password reset) via provider
- [ ] Email verification gate on protected routes
- [ ] Password reset flow end-to-end
- [ ] Account deletion / data export (GDPR-adjacent basics)

## Day 9-10 — Access control & hardening
- [ ] Role-based access control (owner/admin/member)
- [ ] Rate limiting on auth endpoints
- [x] Input validation audit + security headers
- [ ] Secrets management review (no plaintext secrets in repo/CI)

## Day 11-12 — Observability & ops
- [x] Structured logging (backend + frontend)
- [ ] Error tracking (Sentry or equivalent)
- [ ] Health/readiness probes for orchestration
- [ ] Basic usage metrics/analytics hook

## Day 13-14 — Ship prep
- [ ] Production Dockerfiles (multi-stage, non-root, slim images)
- [ ] Deploy target decided + documented (e.g. Fly.io/Render/ECS)
- [ ] Staging environment smoke-tested end-to-end
- [ ] PRD finalized against what actually shipped

## Post-PRD — Maintenance mode
- [ ] Dependency update cadence (Dependabot/Renovate)
- [ ] On-call/monitoring alert routing
- [ ] Changelog process for buyers/licensees

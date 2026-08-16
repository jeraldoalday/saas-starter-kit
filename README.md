# SaaS Starter Kit

A production-lean scaffold for a subscription SaaS product: a **FastAPI**
backend (Python) and a **Next.js** frontend (Node.js/TypeScript), wired up
with authentication, a billing hook, a database layer, tests, and CI.

This is a starting point meant to be sold or licensed as a boilerplate —
it deliberately ships only what a real SaaS needs on day one, so the buyer
isn't paying for dead weight.

## Stack

| Layer     | Tech                                                   |
|-----------|---------------------------------------------------------|
| Backend   | Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Postgres |
| Frontend  | Node.js 20+, Next.js (App Router), TypeScript, Tailwind |
| Auth      | JWT access/refresh tokens, bcrypt password hashing       |
| Billing   | Stripe webhook + subscription model (stubbed, wired up)  |
| Infra     | Docker Compose (Postgres + backend + frontend)           |
| CI        | GitHub Actions (lint + test, backend and frontend)        |

## Project layout

```
.
├── backend/            FastAPI service
│   ├── app/
│   │   ├── api/v1/      versioned routes (health, auth, users, billing)
│   │   ├── core/        settings + security (hashing, JWT)
│   │   ├── models/      SQLAlchemy models
│   │   ├── schemas/     Pydantic request/response schemas
│   │   └── services/    business logic (auth, billing)
│   ├── migrations/      Alembic migrations
│   └── tests/           pytest suite
├── frontend/           Next.js app
│   └── src/
│       ├── app/          routes: landing, (auth) login/register, dashboard
│       ├── components/   UI primitives + feature components
│       ├── lib/          API client, auth helpers, types
│       └── hooks/        client hooks (useAuth)
└── .github/workflows/  CI for backend + frontend
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

## Roadmap to PRD-ready

This scaffold covers the foundation: auth, a data layer, a billing hook,
tests, and CI. Turning it into a sellable product still requires (tracked
in `ROADMAP.md`): real Stripe product/price wiring, email delivery
(verification/reset), role-based access control, rate limiting, structured
logging/observability, and a hardened production Docker/deploy setup.

## License

See [LICENSE.md](./LICENSE.md) — proprietary placeholder, replace before
distribution.

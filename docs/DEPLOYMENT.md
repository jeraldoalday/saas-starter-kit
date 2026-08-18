# Deployment

The target for this starter kit is **Fly.io**: two apps (backend, frontend)
and a managed Postgres, deployed from the Dockerfiles already in the repo.

## Why Fly.io

The decision was between Fly.io, Render and AWS ECS. What settled it:

- **The images are already right.** Both Dockerfiles are multi-stage,
  non-root and expose a health endpoint. Fly deploys a container as-is, so
  nothing about the build has to be reshaped to fit the platform, and moving
  off it later means pointing a different runner at the same images.
- **Postgres is one command away** (`fly postgres create`) and speaks plain
  `DATABASE_URL`, which is what `Settings` already reads.
- **Scheduled work has a home.** `prune-usage-events` needs to run on a
  timer; `fly machine run --schedule` covers it without a second service.
- **ECS is the wrong shape for day one.** It is the better answer once
  there's a VPC, an ALB and IAM roles to manage, and a distraction before
  then. Render is closer to Fly than either is to ECS — pick it instead if
  you would rather have a managed dashboard than a CLI.

Nothing in the application code depends on this choice. The only
platform-specific artefacts are the `fly.toml` files described below.

## Prerequisites

```bash
brew install flyctl      # or: curl -L https://fly.io/install.sh | sh
fly auth login
```

## 1. Database

```bash
fly postgres create --name saas-db --region <region>
fly postgres attach saas-db --app saas-backend
```

Attaching sets `DATABASE_URL` on the backend app. It is set as
`postgres://…`, and SQLAlchemy needs the driver named, so override it:

```bash
fly secrets set --app saas-backend \
  DATABASE_URL="postgresql+psycopg://<user>:<password>@<host>:5432/<db>"
```

## 2. Backend

`backend/fly.toml`:

```toml
app = "saas-backend"
primary_region = "<region>"

[build]
  dockerfile = "Dockerfile"

[env]
  ENVIRONMENT = "production"
  DEBUG = "false"
  ENABLE_HSTS = "true"
  TRUST_PROXY_HEADERS = "true"
  LOG_JSON = "true"
  REQUIRE_EMAIL_VERIFICATION = "true"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "suspend"
  min_machines_running = 1

  [[http_service.checks]]
    path = "/api/v1/health/ready"
    interval = "15s"
    timeout = "2s"
    grace_period = "10s"

[deploy]
  release_command = "alembic upgrade head"
```

Three of those settings are load-bearing:

- `TRUST_PROXY_HEADERS = "true"` is correct **here specifically**, because
  Fly's proxy overwrites `X-Forwarded-For`. On a platform that merely
  appends to it, leave it off — otherwise a client spoofs the header and
  walks past the per-IP rate limits.
- `ENABLE_HSTS = "true"` is safe only because TLS terminates at Fly's edge.
- `release_command` runs the migrations once per deploy, before any machine
  takes traffic. Do not put `alembic upgrade head` in the container's `CMD`:
  every replica would race the others to apply the same migration.

Then set the secrets. These are exactly the values the production config
guard refuses to start without:

```bash
fly secrets set --app saas-backend \
  SECRET_KEY="$(openssl rand -hex 32)" \
  STRIPE_SECRET_KEY="sk_live_…" \
  STRIPE_WEBHOOK_SECRET="whsec_…" \
  STRIPE_PRICE_ID_MONTHLY="price_…" \
  SENTRY_DSN="https://…" \
  FRONTEND_BASE_URL="https://app.example.com" \
  CORS_ORIGINS="https://app.example.com" \
  SMTP_HOST="…" SMTP_USERNAME="…" SMTP_PASSWORD="…" EMAIL_BACKEND="smtp"

fly deploy --app saas-backend --config backend/fly.toml
```

If any of them is missing or still a placeholder, the app fails at boot with
a message naming the offending setting. That is the guard working, not a
deploy failure to route around.

## 3. Frontend

`VITE_` variables are inlined at build time, so the API URL is a build
argument, not a runtime secret:

```bash
fly deploy --app saas-frontend --config frontend/fly.toml \
  --build-arg VITE_API_BASE_URL="https://api.example.com/api/v1" \
  --build-arg VITE_SENTRY_DSN="https://…" \
  --build-arg VITE_SENTRY_ENVIRONMENT="production"
```

`frontend/fly.toml` needs `internal_port = 8080` — the nginx-unprivileged
base image listens there, not on 80.

Changing an API URL means a rebuild and redeploy, not a restart. Anything
sensitive must never be given a `VITE_` prefix; that prefix is what ships it
to the browser.

## 4. Stripe webhook

Point a Stripe webhook endpoint at
`https://api.example.com/api/v1/billing/webhook` and subscribe to
`checkout.session.completed`, `customer.subscription.*` and
`invoice.payment_failed`. Copy the signing secret into
`STRIPE_WEBHOOK_SECRET`. Without it the endpoint fails closed and rejects
every delivery, which is the intended behaviour rather than a
misconfiguration to work around.

## 5. Scheduled retention prune

`usage_events` only grows unless something prunes it:

```bash
fly machine run --app saas-backend \
  --schedule daily \
  --command "python -m app.scripts.prune_usage_events"
```

Check what it would remove first with
`fly ssh console --app saas-backend -C "python -m app.scripts.prune_usage_events --dry-run"`.

## Staging

Run the same two apps with a `-staging` suffix and their own Postgres, with
`ENVIRONMENT=production` (so the config guard stays armed), Stripe **test**
keys, and `SENTRY_ENVIRONMENT=staging`. The only difference from production
should be the credentials.

### Smoke test

Against a fresh staging deploy, in order:

1. `curl https://api-staging.example.com/api/v1/health/ready` → `{"status":"ok"}`.
   A `503` here means the database attachment or `DATABASE_URL` driver
   prefix is wrong.
2. Register an account through the UI; confirm the verification email
   arrives and the link verifies it.
3. Log out, request a password reset, complete it, log back in with the new
   password.
4. Subscribe with Stripe test card `4242 4242 4242 4242`; confirm the
   dashboard shows the plan as active and the webhook was delivered
   (Stripe dashboard → Webhooks → Recent deliveries, all `2xx`).
5. Open the billing portal from the dashboard and cancel; confirm the
   subscription reads `canceled` within a few seconds of the webhook.
6. Reload the dashboard and confirm the activity panel shows the events the
   run above produced — signup, login, checkout, activation.
7. Delete the account; confirm it is gone and that
   `GET /api/v1/analytics/events` (as an admin) no longer returns its rows.

A run that gets through all seven exercises every external dependency:
Postgres, SMTP, Stripe in both directions, and the analytics path.

## Rollback

`fly releases --app saas-backend` then
`fly deploy --image <previous-image> --app saas-backend`.

Migrations are the exception: rolling the image back does not roll a
migration back. Check whether the release you are leaving added one, and
`alembic downgrade -1` deliberately if so. Migration `0006` is a one-way
change by design — it folds email casing that cannot be reconstructed — so
a rollback across it is safe for the schema but does not restore the
original spellings.

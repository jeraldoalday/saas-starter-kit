.PHONY: dev-backend dev-frontend test test-backend test-frontend lint migrate up down sync-stripe-catalog prune-usage-events prune-api-keys prune-sessions prune-audit-logs prune-webhook-deliveries prune check-health-and-alert check-migration-drift deliver-webhooks deliver-webhooks-loop

dev-backend:
	cd backend && uvicorn app.main:app --reload

dev-frontend:
	cd frontend && npm run dev

test: test-backend test-frontend

test-backend:
	cd backend && pytest

test-frontend:
	cd frontend && npm test -- --run

lint:
	cd backend && ruff check .
	cd frontend && npm run lint

migrate:
	cd backend && alembic upgrade head

sync-stripe-catalog:
	cd backend && python -m app.scripts.sync_stripe_catalog

prune-usage-events:
	cd backend && python -m app.scripts.prune_usage_events

prune-api-keys:
	cd backend && python -m app.scripts.prune_api_keys

prune-sessions:
	cd backend && python -m app.scripts.prune_sessions

prune-audit-logs:
	cd backend && python -m app.scripts.prune_audit_logs

prune-webhook-deliveries:
	cd backend && python -m app.scripts.prune_webhook_deliveries

# Everything the scheduled retention job should run.
prune: prune-usage-events prune-api-keys prune-sessions prune-audit-logs prune-webhook-deliveries

# One pass over the outbound webhook queue: what a cron entry or a
# Kubernetes CronJob should call. Nothing queued by the API is POSTed until
# this runs, so a deployment with webhooks enabled and no schedule for this
# has a queue and no delivery.
deliver-webhooks:
	cd backend && python -m app.scripts.deliver_webhooks

# The same thing as a long-lived worker, for when a per-minute cron leaves
# deliveries waiting most of a minute. Run one at a time: the queue has no
# locking, so two workers would send everything twice.
deliver-webhooks-loop:
	cd backend && python -m app.scripts.deliver_webhooks --loop

check-health-and-alert:
	cd backend && python -m app.scripts.check_health_and_alert

# Needs a database already at head; CI runs it against Postgres after
# `alembic upgrade head`.
check-migration-drift:
	cd backend && python -m app.scripts.check_migration_drift

up:
	docker compose up --build

down:
	docker compose down

.PHONY: dev-backend dev-frontend test test-backend test-frontend lint migrate up down sync-stripe-catalog prune-usage-events check-health-and-alert

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

check-health-and-alert:
	cd backend && python -m app.scripts.check_health_and_alert

up:
	docker compose up --build

down:
	docker compose down

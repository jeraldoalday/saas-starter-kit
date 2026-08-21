from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    analytics,
    api_keys,
    auth,
    billing,
    health,
    sessions,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(api_keys.router)
api_router.include_router(sessions.router)
api_router.include_router(billing.router)
api_router.include_router(admin.router)
api_router.include_router(analytics.router)

from fastapi import APIRouter

from app.api.v1.endpoints import admin, auth, billing, health, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(billing.router)
api_router.include_router(admin.router)

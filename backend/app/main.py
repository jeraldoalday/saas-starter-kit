from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware

settings = get_settings()
configure_logging(level=settings.log_level, json_output=settings.log_json)

app = FastAPI(
    title="SaaS Starter API",
    version="0.1.0",
    debug=settings.debug,
)

# Middleware runs in reverse registration order, so RequestIdMiddleware is
# added last to make it outermost: every other layer's logs carry the id.
app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.enable_hsts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)

app.include_router(api_router, prefix="/api/v1")

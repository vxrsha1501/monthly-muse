"""FastAPI application: wiring only - routers stay thin, services orchestrate."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import analytics, auth, catalog, cycles, messages, misc, plans, users
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import setup_logging
from app.db.session import SessionLocal, init_db

logger = logging.getLogger("monthlymuse.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    # global seed data (occasions, topics) - idempotent
    db = SessionLocal()
    try:
        from app.services.catalog_service import seed_global_data
        seed_global_data(db)
    finally:
        db.close()
    # models are loaded once at start-up (Section 10.2)
    from app.core.runtime import Runtime
    Runtime.init()
    from app.scheduler.runner import start_in_process, stop_in_process
    start_in_process()
    logger.info("app_started env=%s provider=%s", settings.environment, settings.llm_provider)
    yield
    stop_in_process()


def create_app() -> FastAPI:
    app = FastAPI(
        title="MonthlyMuse API",
        version="0.1.0",
        description="AI-Powered Automated Message Generator for Monthly Posting",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    prefix = settings.api_v1
    app.include_router(auth.router, prefix=prefix)
    app.include_router(users.router, prefix=prefix)
    app.include_router(catalog.router, prefix=prefix)
    app.include_router(plans.router, prefix=prefix)
    app.include_router(cycles.router, prefix=prefix)
    app.include_router(messages.router, prefix=prefix)
    app.include_router(analytics.router, prefix=prefix)
    app.include_router(misc.router, prefix=prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"name": settings.app_name, "docs": "/api/docs", "health": f"{prefix}/admin/health"}

    return app


app = create_app()

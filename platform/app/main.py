from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import accounts as accounts_api
from app.api import agent as agent_api
from app.api import dashboard as dashboard_api
from app.api import data as data_api
from app.api import recon as recon_api
from app.api import tasks as tasks_api
from app.config import settings
from app.db import SessionLocal, init_db
from app.seed import seed
from app.services.executor import Worker
from app.services.scheduler import Scheduler
from app.web.routes import router as web_router

PUBLIC_PREFIXES = ("/static", "/login", "/health", "/docs", "/openapi.json", "/api/agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        seed(db)

    workers: list = []
    if settings.enable_worker:
        worker = Worker()
        worker.start()
        workers.append(worker)
    if settings.enable_scheduler:
        scheduler = Scheduler()
        scheduler.start()
        workers.append(scheduler)
    app.state.workers = workers
    try:
        yield
    finally:
        for item in workers:
            item.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="银行财务平台", lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(settings.base_dir / "app" / "web" / "static")), name="static")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return await call_next(request)
        from app.services.security import verify_session

        if verify_session(request.cookies.get("session")):
            return await call_next(request)
        if path.startswith("/api"):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "未登录"}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    app.include_router(web_router)
    app.include_router(accounts_api.router)
    app.include_router(tasks_api.router)
    app.include_router(data_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(recon_api.router)
    app.include_router(agent_api.router)

    @app.get("/health")
    def health():
        return {"ok": True, "worker": settings.enable_worker, "scheduler": settings.enable_scheduler}

    return app


app = create_app()

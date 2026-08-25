"""FastAPI app entrypoint: mounts static files/templates, wires up routes,
initializes the DB, and starts the APScheduler on boot."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import auth_router, router
from app.auth import NotAuthenticatedError, is_using_default_password, require_login
from app.config import BACKEND_DIR, get_session_secret, settings
from app.live.scheduler import start_scheduler, stop_scheduler
from app.logging_setup import configure_logging
from app.models.db import init_db, log_event

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    log_event("INFO", "app", "Application starting up.")
    if is_using_default_password():
        log_event(
            "WARNING",
            "app",
            "Using the default login password - set SWING_AUTH_USERNAME / SWING_AUTH_PASSWORD "
            "(host secrets in production, backend/.env locally) before exposing this beyond your own machine.",
        )
    start_scheduler()
    yield
    stop_scheduler()
    log_event("INFO", "app", "Application shutting down.")


app = FastAPI(title="Reliance ICT/SMC Weekly Swing Strategy System", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=get_session_secret(), same_site="lax", max_age=60 * 60 * 24 * 30)


@app.exception_handler(NotAuthenticatedError)
async def _not_authenticated_handler(request: Request, exc: NotAuthenticatedError):
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
# NOTE: reports/snapshots are intentionally NOT static-mounted - they're
# served through routes.py's authenticated router (download_report /
# get_snapshot) so a logged-out visitor can't reach trade data by guessing a
# filename.
app.include_router(auth_router)
app.include_router(router, dependencies=[Depends(require_login)])

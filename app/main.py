"""ise-ndg-sync — Catalyst Center → ISE Network Device Group sync service."""
import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import LOG_LEVEL, APP_VERSION
from .db import init_db
from .api import router
from . import scheduler
from .settings_store import get_setting

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("ise-ndg-sync")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    log.info("ise-ndg-sync %s started", APP_VERSION)
    yield
    scheduler.scheduler.shutdown(wait=False)


app = FastAPI(title="ise-ndg-sync", version=APP_VERSION, lifespan=lifespan)


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    """Optional single-admin login for the GUI (nice-to-have). Webhook + healthz
    stay open — the webhook has its own shared-token check."""
    password = get_setting("ui.admin_password")
    path = request.url.path
    if password and not (path == "/healthz" or path.startswith("/webhook/")):
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = user == "admin" and secrets.compare_digest(pw, password)
            except Exception:  # noqa: BLE001 - malformed header = unauthorized
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ise-ndg-sync"'})
    return await call_next(request)


app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

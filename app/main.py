"""FastAPI app: pages, JSON API and the game WebSocket."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .bot_adapter import BotPool
from .matches import MatchManager
from .security import RateLimiter, Signer, validate_username

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
PRODUCTION = os.environ.get("APP_ENV", "development").lower() == "production"
COOKIE = "pk_session"
TOKEN_MAX_AGE = 365 * 86400
NAME_HOLD = float(os.environ.get("NAME_HOLD_DAYS", "30")) * 86400
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1" if PRODUCTION else "0") == "1"

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("poker")

_secret = os.environ.get("SECRET_KEY")
if not _secret:
    if PRODUCTION:
        raise RuntimeError("SECRET_KEY must be set in production")
    _secret = secrets.token_hex(32)
    log.warning("SECRET_KEY not set; using a random key (sessions reset on restart)")
signer = Signer(_secret)

# Abuse limits (per sliding window). All configurable.
match_ip_limit = RateLimiter(int(os.environ.get("MATCH_PER_IP_PER_HOUR", "20")), 3600)
match_session_limit = RateLimiter(int(os.environ.get("MATCH_PER_SESSION_PER_15MIN", "5")), 900)
session_ip_limit = RateLimiter(int(os.environ.get("SESSIONS_PER_IP_PER_HOUR", "30")), 3600)
api_ip_limit = RateLimiter(int(os.environ.get("API_PER_IP_PER_MIN", "120")), 60)

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    pool = BotPool()
    manager = MatchManager(pool)
    state["pool"], state["manager"] = pool, manager

    async def sweeper():
        while True:
            await asyncio.sleep(30)
            try:
                await manager.sweep()
                for rl in (match_ip_limit, match_session_limit, session_ip_limit, api_ip_limit):
                    rl.sweep()
            except Exception:
                log.exception("sweep failed")

    task = asyncio.create_task(sweeper())
    yield
    task.cancel()
    pool.close()


app = FastAPI(debug=False, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ------------------------------------------------------------------ hardening

def client_ip(scope_headers, fallback: str) -> str:
    """Client IP behind a known proxy. See README: CLIENT_IP_HEADER / TRUSTED_PROXY_HOPS."""
    hdr = os.environ.get("CLIENT_IP_HEADER")
    if hdr:
        v = scope_headers.get(hdr.lower())
        if v:
            return v.strip()
    hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))
    if hops > 0:
        xff = [p.strip() for p in (scope_headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        if len(xff) >= hops:
            return xff[-hops]
    return fallback


def _ip(request: Request) -> str:
    return client_ip(request.headers, request.client.host if request.client else "?")


def _same_origin(headers) -> bool:
    origin = headers.get("origin")
    if not origin:
        return True                       # same-origin GETs and non-browser clients
    host = headers.get("host", "")
    allowed = {h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
    return urlparse(origin).netloc in ({host} | allowed)


@app.middleware("http")
async def harden(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        if not api_ip_limit.allow(_ip(request)):
            return JSONResponse({"error": "Too many requests. Slow down."}, status_code=429)
        if request.method != "GET":
            if not _same_origin(request.headers):
                return JSONResponse({"error": "Bad origin."}, status_code=403)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"error": "Expected JSON."}, status_code=415)
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error")
        response = JSONResponse({"error": "Internal error."}, status_code=500)
    host = request.headers.get("host", "")
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        f"connect-src 'self' wss://{host} ws://{host}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    msg = "Not found." if exc.status_code == 404 else "Request failed."
    return JSONResponse({"error": msg}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def any_error(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse({"error": "Internal error."}, status_code=500)


# ------------------------------------------------------------------ sessions

def _session(request_or_ws) -> str | None:
    return signer.verify(request_or_ws.cookies.get(COOKIE), TOKEN_MAX_AGE)


def _username_for(sid: str) -> str | None:
    rows = db._q("SELECT username FROM users WHERE session_id=? ORDER BY last_seen DESC LIMIT 1", (sid,))
    return rows[0]["username"] if rows else None


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 2048:
        return {}
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


# ------------------------------------------------------------------ pages

def _page(name: str):
    return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})


@app.get("/")
async def landing():
    return _page("index.html")


@app.get("/play")
async def play_page():
    return _page("play.html")


@app.get("/leaderboard")
async def leaderboard_page():
    return _page("leaderboard.html")


@app.get("/healthz")
async def health():
    return {"ok": True}


# Only the static/ folder is ever served as files. bot.py and app/ live outside it.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ------------------------------------------------------------------ API

@app.get("/api/me")
async def me(request: Request):
    sid = _session(request)
    if not sid:
        return {"username": None, "active_match": False}
    m = state["manager"].current(sid)
    return {"username": _username_for(sid), "active_match": bool(m and m.status == "playing")}


@app.post("/api/session")
async def create_session(request: Request):
    body = await _json_body(request)
    name = body.get("username")
    err = validate_username(name)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    sid = _session(request)
    token = None
    if not sid:
        if not session_ip_limit.allow(_ip(request)):
            return JSONResponse({"error": "Too many new sessions from your network. Try later."}, status_code=429)
        sid, token = signer.issue()
    m = state["manager"].current(sid)
    if m and m.status == "playing" and m.username.lower() != name.lower():
        return JSONResponse({"error": "Finish or quit your current match before changing name."}, status_code=409)
    if not db.claim_username(name, sid, NAME_HOLD):
        return JSONResponse({"error": "That name is in use by another player."}, status_code=409)
    resp = JSONResponse({"username": name})
    if token:
        resp.set_cookie(COOKIE, token, max_age=TOKEN_MAX_AGE, httponly=True, secure=COOKIE_SECURE,
                        samesite="lax", path="/")
    return resp


@app.post("/api/matches")
async def create_match(request: Request):
    sid = _session(request)
    name = _username_for(sid) if sid else None
    if not sid or not name or not db.owns_username(name, sid):
        return JSONResponse({"error": "Choose a username first."}, status_code=401)
    manager: MatchManager = state["manager"]
    if manager.active_count() >= manager.max_active:
        return JSONResponse({"error": "The tables are full right now. Try again in a few minutes."},
                            status_code=503)
    if not match_session_limit.allow(sid) or not match_ip_limit.allow(_ip(request)):
        return JSONResponse({"error": "You are starting matches too quickly. Try again later."},
                            status_code=429)
    db.touch_user(name, sid)
    m = await manager.create(sid, name)
    return {"match_id": m.id, "hands": m.hands_total}


@app.post("/api/matches/current/quit")
async def quit_match(request: Request):
    sid = _session(request)
    m = state["manager"].current(sid) if sid else None
    if not m or m.status != "playing":
        return JSONResponse({"error": "No match in progress."}, status_code=404)
    await state["manager"].abandon(m, "you left the match")
    return {"ok": True}


@app.get("/api/leaderboard")
async def leaderboard():
    hands = state["manager"].hands_total
    return {"entries": db.leaderboard(hands, 100), "bot": db.bot_record(), "hands_per_match": hands}


# ------------------------------------------------------------------ WebSocket

@app.websocket("/ws")
async def game_socket(ws: WebSocket):
    if not _same_origin(ws.headers):
        await ws.close(code=1008)
        return
    sid = _session(ws)
    manager: MatchManager = state["manager"]
    m = manager.current(sid) if sid else None
    await ws.accept()
    if not m:
        await ws.send_json({"type": "no_match"})
        await ws.close()
        return
    m.sockets.add(ws)
    msg_limit = RateLimiter(int(os.environ.get("WS_MSGS_PER_2S", "12")), 2.0)
    try:
        await ws.send_json({"type": "state", "state": manager.view(m)})
        while True:
            raw = await ws.receive_text()
            if len(raw) > 512:
                await ws.send_json({"type": "error", "error": "Message too large."})
                continue
            if not msg_limit.allow("x"):
                await ws.send_json({"type": "error", "error": "Too many messages."})
                continue
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError
            except ValueError:
                await ws.send_json({"type": "error", "error": "Malformed message."})
                continue
            if msg.get("type") == "sync":
                await ws.send_json({"type": "state", "state": manager.view(m)})
                continue
            err = await manager.human_action(m, msg)
            if err:
                await ws.send_json({"type": "error", "error": err})
                await ws.send_json({"type": "state", "state": manager.view(m)})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("socket error")
    finally:
        m.sockets.discard(ws)

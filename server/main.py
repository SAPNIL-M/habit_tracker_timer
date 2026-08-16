"""Profile stopwatch backend.

The stopwatch counts time ONLY while the authorized Chrome profile is in use.
The profile is configured in server/config.json (or the STOPWATCH_EMAIL
environment variable). The Chrome extension reports profile activity; the web
page also heartbeats while visible. If no liveness signal arrives for
SIGNAL_TIMEOUT_S, the stopwatch pauses (with auto-resume armed). When activity
returns, it resumes from where it left off.

State machine:
    status: stopped | running | paused
    While running, the authoritative elapsed time is:
        accumulated_ms + (now - started_at_ms)
    accumulated_ms holds everything counted before the current run segment.
"""
import asyncio
import json
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "stopwatch.db"

CONFIG_PATH = BASE_DIR / "config.json"


def load_expected_email() -> str:
    """Authorized profile email from STOPWATCH_EMAIL or server/config.json."""
    env = os.environ.get("STOPWATCH_EMAIL", "").strip()
    if env:
        return env.lower()
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        email = str(cfg.get("expected_email", "")).strip().lower()
        if email:
            return email
    except Exception:
        pass
    return ""


EXPECTED_EMAIL = load_expected_email()
SIGNAL_TIMEOUT_S = 120.0  # no liveness signal for this long -> profile considered left
TICK_S = 3.0              # watchdog interval
PORT = 8765

_mutex = threading.Lock()


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL DEFAULT 'stopped',
                accumulated_ms INTEGER NOT NULL DEFAULT 0,
                started_at_ms INTEGER,
                auto_resume INTEGER NOT NULL DEFAULT 0,
                last_signal_ms INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY,
                email TEXT NOT NULL
            )"""
        )
        # migrate old tokens schema (email as primary key) if present; tokens
        # are re-registered automatically by the extension, so dropping is safe
        info = conn.execute("PRAGMA table_info(tokens)").fetchall()
        pk_cols = [r["name"] for r in info if r["pk"] > 0]
        if pk_cols and pk_cols[0] == "email":
            conn.execute("DROP TABLE tokens")
            conn.execute(
                """CREATE TABLE tokens (
                    token TEXT PRIMARY KEY,
                    email TEXT NOT NULL
                )"""
            )
        conn.execute(
            "INSERT OR IGNORE INTO state (id, status, last_signal_ms) VALUES (1, 'stopped', ?)",
            (int(time.time() * 1000),),
        )


def load_state() -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM state WHERE id = 1").fetchone()
    return dict(row)


def save_state(st: dict):
    with _connect() as conn:
        conn.execute(
            """UPDATE state
               SET status=?, accumulated_ms=?, started_at_ms=?, auto_resume=?, last_signal_ms=?
               WHERE id=1""",
            (st["status"], st["accumulated_ms"], st["started_at_ms"], st["auto_resume"], st["last_signal_ms"]),
        )


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _close_stale_segment(st: dict, now_ms: int):
    """If the profile went quiet long enough to count as 'left', close the run
    segment at the moment it went quiet and reopen it now (auto-resume)."""
    timeout_ms = int(SIGNAL_TIMEOUT_S * 1000)
    if (
        st["status"] == "running"
        and st["last_signal_ms"]
        and now_ms - st["last_signal_ms"] > timeout_ms
    ):
        st["accumulated_ms"] += (st["last_signal_ms"] + timeout_ms) - st["started_at_ms"]
        st["started_at_ms"] = now_ms


def transition(st: dict, kind: str) -> dict:
    """Apply one event to the state. Events:
    start, pause (explicit), heartbeat (page), alive / inactive (extension),
    reset."""
    now = _now_ms()

    if kind == "start":
        _close_stale_segment(st, now)
        if st["status"] != "running":
            st["status"] = "running"
            st["started_at_ms"] = now
            st["auto_resume"] = 0
        st["last_signal_ms"] = now

    elif kind == "pause":
        if st["status"] == "running":
            st["accumulated_ms"] += now - st["started_at_ms"]
            st["status"] = "paused"
            st["auto_resume"] = 0
        st["last_signal_ms"] = now

    elif kind == "heartbeat":
        # Page is alive but must NOT auto-resume: the page stays "visible"
        # even when its window is covered by another app/profile, so only the
        # extension's focus signal ("alive") may resume a paused stopwatch.
        _close_stale_segment(st, now)
        st["last_signal_ms"] = now

    elif kind == "alive":
        _close_stale_segment(st, now)
        if st["status"] == "paused" and st["auto_resume"]:
            st["status"] = "running"
            st["started_at_ms"] = now
        st["last_signal_ms"] = now

    elif kind == "inactive":
        if st["status"] == "running":
            st["accumulated_ms"] += now - st["started_at_ms"]
            st["status"] = "paused"
            st["auto_resume"] = 1
        st["last_signal_ms"] = now

    elif kind == "timeout":
        # No closing event arrived (browser killed, laptop slept, event
        # missed): close the segment where the profile went quiet.
        timeout_ms = int(SIGNAL_TIMEOUT_S * 1000)
        if st["status"] == "running" and st["last_signal_ms"]:
            st["accumulated_ms"] += (st["last_signal_ms"] + timeout_ms) - st["started_at_ms"]
            st["status"] = "paused"
            st["auto_resume"] = 1
        st["last_signal_ms"] = now

    elif kind == "reset":
        st["status"] = "stopped"
        st["accumulated_ms"] = 0
        st["started_at_ms"] = None
        st["auto_resume"] = 0
        st["last_signal_ms"] = now

    return st


# --------------------------------------------------------------------------
# Watchdog: pause if the profile goes quiet (browser killed, laptop asleep,
# extension event missed).
# --------------------------------------------------------------------------

async def _watchdog():
    timeout_ms = int(SIGNAL_TIMEOUT_S * 1000)
    while True:
        await asyncio.sleep(TICK_S)
        now = _now_ms()
        with _mutex:
            st = load_state()
            if st["status"] == "running" and st["last_signal_ms"] and now - st["last_signal_ms"] > timeout_ms:
                save_state(transition(st, "timeout"))


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    task = asyncio.create_task(_watchdog())
    yield
    task.cancel()


app = FastAPI(title="Profile Stopwatch", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

class RegisterBody(BaseModel):
    email: str
    token: str


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def authorize(request: Request) -> str:
    token = _token_from(request)
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    with _connect() as conn:
        row = conn.execute("SELECT email FROM tokens WHERE token = ?", (token,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return row["email"]


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    """Public config the page and extension need before they hold a token."""
    return {"expected_email": EXPECTED_EMAIL}


@app.post("/api/extension/register")
def register(body: RegisterBody):
    """Register a token for this profile's extension.

    Two tiers:
    - email matches EXPECTED_EMAIL       -> fully verified (strictest)
    - email is empty                     -> presence gate: the profile isn't
      signed in to Chrome, so Chrome can't report its email. The lock is then
      "this profile has the extension installed", which still restricts the
      stopwatch to the profile(s) where the user chose to install it.
    - any other (signed-in) email        -> hard block (403)
    """
    email = body.email.strip().lower()
    if email != EXPECTED_EMAIL.lower() and email != "":
        raise HTTPException(status_code=403, detail="profile not allowed")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tokens (token, email) VALUES (?, ?)",
            (body.token, email),
        )
    return {"ok": True}


def _mutate(kind: str):
    async def handler(request: Request):
        authorize(request)
        if kind in ("alive", "inactive"):
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            print(f"[ext] {kind} windows={payload.get('windows')}", flush=True)
        with _mutex:
            st = load_state()
            st = transition(st, kind)
            save_state(st)
        return {"ok": True, "status": st["status"], "auto_resume": bool(st["auto_resume"])}
    return handler


@app.get("/api/state")
def get_state(request: Request):
    authorize(request)
    st = load_state()
    return {
        "status": st["status"],
        "accumulated_ms": st["accumulated_ms"],
        "started_at_ms": st["started_at_ms"],
        "auto_resume": bool(st["auto_resume"]),
        "server_time_ms": _now_ms(),
    }


_MUTATE_ROUTES = [
    ("/api/start", "start"),
    ("/api/pause", "pause"),
    ("/api/heartbeat", "heartbeat"),
    ("/api/reset", "reset"),
    ("/api/extension/alive", "alive"),
    ("/api/extension/inactive", "inactive"),
]
for _path, _kind in _MUTATE_ROUTES:
    app.add_api_route(_path, _mutate(_kind), methods=["POST"])


@app.post("/api/toggle")
async def toggle(request: Request):
    authorize(request)
    with _mutex:
        st = load_state()
        st = transition(st, "pause" if st["status"] == "running" else "start")
        save_state(st)
    return {"ok": True, "status": st["status"], "auto_resume": bool(st["auto_resume"])}


if __name__ == "__main__":
    import uvicorn

    init_db()
    if not EXPECTED_EMAIL:
        print("WARNING: no expected email configured — create server/config.json")
        print('         with {"expected_email": "you@gmail.com"} (it is git-ignored).')
    print(f"Profile stopwatch running at  http://127.0.0.1:{PORT}")
    print("Open this URL in the Chrome profile set in server/config.json (pin the tab).")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")

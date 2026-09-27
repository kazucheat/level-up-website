# backend/main.py
import os
import json
import sqlite3
import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError

from bot_engine import bot_state, start_bot_for_account

BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
DB_PATH = BASE_DIR / "users.db"

SECRET_KEY = os.environ.get("SECRET_KEY", "change_me_in_production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@nxc.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="NXC LEVEL UP")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ==================== DB ====================
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            email TEXT UNIQUE,
            password_hash TEXT,
            role TEXT DEFAULT 'user',
            plan TEXT DEFAULT 'free',
            plan_expires INTEGER,
            created_at INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            uid TEXT,
            password TEXT,
            nickname TEXT,
            region TEXT DEFAULT 'BD',
            initial_exp INTEGER DEFAULT 0,
            current_exp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            created_at INTEGER
        )
    """)
    conn.commit()

    # create admin if not exist
    row = c.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
    if not row:
        uid = "u-" + secrets.token_hex(8)
        c.execute(
            "INSERT INTO users (id, username, email, password_hash, role, plan, created_at) VALUES (?, ?, ?, ?, 'admin', 'safe', ?)",
            (uid, "admin", ADMIN_EMAIL, pwd_ctx.hash(ADMIN_PASSWORD), int(datetime.now().timestamp()))
        )
        conn.commit()
        print(f"[NXC] admin created: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    conn.close()


init_db()


# ==================== AUTH HELPERS ====================
def hash_pw(p): return pwd_ctx.hash(p)
def verify_pw(p, h): return pwd_ctx.verify(p, h)


def make_token(user_id: str):
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    uid = decode_token(authorization[7:])
    if not uid:
        raise HTTPException(401, "Invalid token")
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "User not found")
    return dict(row)


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    return user


# ==================== MODELS ====================
class SignupReq(BaseModel):
    username: str
    email: str
    password: str


class LoginReq(BaseModel):
    email: str
    password: str


class AddAccountReq(BaseModel):
    uid: str
    password: str


class SetPlanReq(BaseModel):
    user_id: str
    plan: str


# ==================== AUTH ROUTES ====================
@app.post("/api/auth/signup")
async def signup(req: SignupReq):
    if not req.email or "@" not in req.email:
        raise HTTPException(400, "Invalid email")
    if len(req.password) < 6:
        raise HTTPException(400, "Password min 6 chars")

    conn = db()
    exists = conn.execute("SELECT id FROM users WHERE email = ?", (req.email,)).fetchone()
    if exists:
        conn.close()
        raise HTTPException(400, "Email already registered")

    uid = "u-" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO users (id, username, email, password_hash, role, plan, created_at) VALUES (?, ?, ?, ?, 'user', 'free', ?)",
        (uid, req.username or req.email.split("@")[0], req.email, hash_pw(req.password), int(datetime.now().timestamp()))
    )
    conn.commit()
    conn.close()

    token = make_token(uid)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": uid, "username": req.username, "email": req.email, "role": "user", "plan": "free"}
    }


@app.post("/api/auth/login")
async def login(req: LoginReq):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (req.email,)).fetchone()
    conn.close()
    if not row or not verify_pw(req.password, row["password_hash"]):
        raise HTTPException(400, "Invalid credentials")

    u = dict(row)
    token = make_token(u["id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": u["id"], "username": u["username"], "email": u["email"], "role": u["role"], "plan": u["plan"]}
    }


@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "username": user["username"], "email": user["email"], "role": user["role"], "plan": user["plan"]}


# ==================== ACCOUNT ROUTES ====================
def check_plan_limit(user: dict) -> int:
    """Return max accounts allowed for plan."""
    limits = {"free": 0, "starting": 3, "basic": 3, "premium": 4, "safe": 5}
    plan = user.get("plan", "free")
    expires = user.get("plan_expires")
    if plan != "free" and expires and expires < int(datetime.now().timestamp()):
        return 0
    return limits.get(plan, 0)


@app.post("/api/account/add")
async def add_account(req: AddAccountReq, user: dict = Depends(get_current_user)):
    limit = check_plan_limit(user)
    if limit == 0:
        raise HTTPException(403, "No active plan. Buy a plan to add accounts.")

    conn = db()
    count = conn.execute("SELECT COUNT(*) as c FROM user_accounts WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    if count >= limit:
        conn.close()
        raise HTTPException(400, f"Plan limit reached ({limit} accounts)")

    conn.execute(
        "INSERT INTO user_accounts (user_id, uid, password, created_at) VALUES (?, ?, ?, ?)",
        (user["id"], req.uid.strip(), req.password.strip(), int(datetime.now().timestamp()))
    )
    conn.commit()
    conn.close()

    # start bot
    asyncio.create_task(start_bot_for_account(user["id"], req.uid.strip(), req.password.strip()))

    return {"ok": True, "uid": req.uid}


@app.post("/api/account/delete")
async def delete_account(req: AddAccountReq, user: dict = Depends(get_current_user)):
    conn = db()
    conn.execute("DELETE FROM user_accounts WHERE user_id = ? AND uid = ?", (user["id"], req.uid))
    conn.commit()
    conn.close()
    bot_state.stop_bot(user["id"], req.uid)
    return {"ok": True}


@app.get("/api/stats")
async def stats(user: dict = Depends(get_current_user)):
    # owner sees own data, admin sees all
    if user.get("role") == "admin":
        accts = bot_state.get_all_accounts()
    else:
        accts = bot_state.get_user_accounts(user["id"])

    total_gained = sum(a.get("gained_exp", 0) for a in accts)
    total_matches = sum(a.get("matches_played", 0) for a in accts)
    logs = bot_state.get_logs(user["id"] if user.get("role") != "admin" else None)

    return {
        "total_accounts": len(accts),
        "total_gained_exp": total_gained,
        "total_matches": total_matches,
        "accounts": accts,
        "logs": logs[-60:],
        "plan": user.get("plan", "free")
    }


# ==================== ADMIN ====================
@app.get("/api/admin/users")
async def admin_users(user: dict = Depends(require_admin)):
    conn = db()
    rows = conn.execute("SELECT id, username, email, role, plan, plan_expires, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["bot_count"] = len(bot_state.get_user_accounts(d["id"]))
        result.append(d)
    return result


@app.post("/api/admin/set-plan")
async def admin_set_plan(req: SetPlanReq, user: dict = Depends(require_admin)):
    durations = {"free": 0, "starting": 1, "basic": 2, "premium": 3, "safe": 7}
    days = durations.get(req.plan, 0)
    expires = int((datetime.now() + timedelta(days=days)).timestamp()) if days else None
    conn = db()
    conn.execute("UPDATE users SET plan = ?, plan_expires = ? WHERE id = ?", (req.plan, expires, req.user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "plan": req.plan, "expires": expires}


# ==================== FRONTEND ====================
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


def page(name: str):
    p = FRONTEND_DIR / name
    if p.exists():
        return FileResponse(str(p))
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/")
async def root(): return page("index.html")

@app.get("/login")
async def login_page(): return page("login.html")

@app.get("/signup")
async def signup_page(): return page("signup.html")

@app.get("/dashboard")
async def dashboard_page(): return page("dashboard.html")

@app.get("/pricing")
async def pricing_page(): return page("pricing.html")

@app.get("/admin")
async def admin_page(): return page("admin.html")

@app.get("/buy")
async def buy_page(request: Request): return page("pricing.html")
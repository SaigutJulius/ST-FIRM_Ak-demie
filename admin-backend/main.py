"""
ST-Firm Akädemie — Admin Backend
FastAPI + SQLite + JWT login + image upload/optimize + audit log.

Run locally:
    pip install -r requirements.txt
    cp .env.example .env   # then edit
    python seed.py         # creates admin user + imports current gallery
    uvicorn main:app --reload --port 8000
Open  http://localhost:8000/admin
"""
import os, io, re, time, json, sqlite3, hashlib, secrets
from pathlib import Path
from datetime import datetime, timezone

import jwt
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from PIL import Image

# ── Config ──────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
DATA = BASE / "data";        DATA.mkdir(exist_ok=True)
UPLOADS = DATA / "uploads";  UPLOADS.mkdir(exist_ok=True)
DB_PATH = DATA / "akademie.db"
STATIC = BASE / "static"

JWT_SECRET     = os.environ.get("JWT_SECRET", "dev-insecure-secret-change-me")
ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
PUBLIC_BASE    = os.environ.get("PUBLIC_BASE", "").rstrip("/")
TOKEN_HOURS    = 12
MAX_DIM        = 1600

# ── DB helpers ──────────────────────────────────────────────────────
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
      CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw_hash TEXT, salt TEXT);
      CREATE TABLE IF NOT EXISTS gallery (
        id INTEGER PRIMARY KEY, filename TEXT, caption TEXT,
        sort_order INTEGER, created_at TEXT);
      CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY, action TEXT, detail TEXT, ts TEXT);
    """)
    c.commit()
    # seed admin from env if not present
    if ADMIN_PASSWORD:
        row = c.execute("SELECT id FROM users WHERE username=?", (ADMIN_USER,)).fetchone()
        if not row:
            salt = secrets.token_hex(16)
            c.execute("INSERT INTO users(username, pw_hash, salt) VALUES(?,?,?)",
                      (ADMIN_USER, hash_pw(ADMIN_PASSWORD, salt), salt))
            c.commit()
    c.close()

def hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()

def log(action: str, detail: str = ""):
    c = db()
    c.execute("INSERT INTO audit(action, detail, ts) VALUES(?,?,?)",
              (action, detail, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    c.commit(); c.close()

# ── Auth ────────────────────────────────────────────────────────────
def make_token(username: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": username, "iat": now, "exp": now + TOKEN_HOURS*3600},
                      JWT_SECRET, algorithm="HS256")

def require_admin(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    try:
        return jwt.decode(auth[7:], JWT_SECRET, algorithms=["HS256"])["sub"]
    except Exception:
        raise HTTPException(401, "Session expired — please log in again")

# ── Image pipeline ──────────────────────────────────────────────────
def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(name)[0].lower()).strip("-")
    return s or "image"

def save_optimized(raw: bytes, orig_name: str) -> str:
    im = Image.open(io.BytesIO(raw))
    w, h = im.size
    scale = min(1.0, MAX_DIM / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    transparent = im.mode in ("RGBA", "P", "LA")
    fn = f"{slugify(orig_name)}-{int(time.time())}" + (".png" if transparent else ".jpg")
    path = UPLOADS / fn
    if transparent:
        im.save(path, optimize=True)
    else:
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(path, quality=86, optimize=True, progressive=True)
    return fn

def img_url(request: Request, filename: str) -> str:
    base = PUBLIC_BASE or str(request.base_url).rstrip("/")
    return f"{base}/uploads/{filename}"

def share_url(request: Request, gid: int) -> str:
    base = PUBLIC_BASE or str(request.base_url).rstrip("/")
    return f"{base}/share/{gid}"

# ── App ─────────────────────────────────────────────────────────────
app = FastAPI(title="ST-Firm Akädemie Admin", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if ALLOWED_ORIGIN == "*" else [ALLOWED_ORIGIN],
    allow_methods=["*"], allow_headers=["*"],
)
init_db()
app.mount("/uploads", StaticFiles(directory=str(UPLOADS)), name="uploads")

class LoginIn(BaseModel):
    username: str
    password: str

class CaptionIn(BaseModel):
    caption: str | None = None
    sort_order: int | None = None

# ── Routes: auth ────────────────────────────────────────────────────
@app.post("/api/login")
def login(body: LoginIn, request: Request):
    c = db()
    row = c.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    c.close()
    if not row or hash_pw(body.password, row["salt"]) != row["pw_hash"]:
        log("login_failed", body.username)
        raise HTTPException(401, "Wrong username or password")
    log("login", body.username)
    return {"token": make_token(body.username), "user": body.username}

# ── Routes: gallery (public read, admin write) ──────────────────────
@app.get("/api/gallery")
def list_gallery(request: Request):
    c = db()
    rows = c.execute("SELECT * FROM gallery ORDER BY sort_order ASC, id ASC").fetchall()
    c.close()
    return [{
        "id": r["id"],
        "src": img_url(request, r["filename"]),
        "caption": r["caption"] or "",
        "share": share_url(request, r["id"]),
        "order": r["sort_order"],
    } for r in rows]

@app.post("/api/gallery")
async def add_image(request: Request, file: UploadFile = File(...),
                    caption: str = Form(""), user: str = Depends(require_admin)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    try:
        fn = save_optimized(raw, file.filename or "image")
    except Exception as e:
        raise HTTPException(400, f"Not a valid image: {e}")
    c = db()
    nxt = (c.execute("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM gallery").fetchone()["n"])
    c.execute("INSERT INTO gallery(filename, caption, sort_order, created_at) VALUES(?,?,?,?)",
              (fn, caption, nxt, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    gid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    c.commit(); c.close()
    log("upload", f"{fn} :: {caption}")
    return {"id": gid, "src": img_url(request, fn), "caption": caption, "share": share_url(request, gid), "order": nxt}

@app.patch("/api/gallery/{gid}")
def edit_image(gid: int, body: CaptionIn, user: str = Depends(require_admin)):
    c = db()
    row = c.execute("SELECT * FROM gallery WHERE id=?", (gid,)).fetchone()
    if not row:
        c.close(); raise HTTPException(404, "Not found")
    cap = body.caption if body.caption is not None else row["caption"]
    order = body.sort_order if body.sort_order is not None else row["sort_order"]
    c.execute("UPDATE gallery SET caption=?, sort_order=? WHERE id=?", (cap, order, gid))
    c.commit(); c.close()
    log("edit", f"id={gid}")
    return {"ok": True}

@app.delete("/api/gallery/{gid}")
def delete_image(gid: int, user: str = Depends(require_admin)):
    c = db()
    row = c.execute("SELECT * FROM gallery WHERE id=?", (gid,)).fetchone()
    if not row:
        c.close(); raise HTTPException(404, "Not found")
    c.execute("DELETE FROM gallery WHERE id=?", (gid,))
    c.commit(); c.close()
    try:
        (UPLOADS / row["filename"]).unlink(missing_ok=True)
    except Exception:
        pass
    log("delete", row["filename"])
    return {"ok": True}

# ── Routes: monitoring ──────────────────────────────────────────────
@app.get("/api/stats")
def stats(user: str = Depends(require_admin)):
    c = db()
    total = c.execute("SELECT COUNT(*) AS n FROM gallery").fetchone()["n"]
    recent = c.execute("SELECT action, detail, ts FROM audit ORDER BY id DESC LIMIT 12").fetchall()
    logins = c.execute("SELECT COUNT(*) AS n FROM audit WHERE action='login'").fetchone()["n"]
    fails = c.execute("SELECT COUNT(*) AS n FROM audit WHERE action='login_failed'").fetchone()["n"]
    c.close()
    return {
        "images": total, "logins": logins, "failed_logins": fails,
        "recent": [dict(r) for r in recent],
    }

# ── Routes: dynamic OG share page (per image) ───────────────────────
@app.get("/share/{gid}", response_class=HTMLResponse)
def share_page(gid: int, request: Request):
    c = db()
    row = c.execute("SELECT * FROM gallery WHERE id=?", (gid,)).fetchone()
    c.close()
    site = ALLOWED_ORIGIN if ALLOWED_ORIGIN != "*" else ""
    gallery_url = f"{site}/ST-FIRM_Ak-demie/#gallery" if site else "/#gallery"
    if not row:
        return RedirectResponse(gallery_url)
    title = (row["caption"] or "ST-Firm Akädemie").replace('"', "&quot;")
    image = img_url(request, row["filename"])
    return HTMLResponse(f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title} — ST-Firm Akädemie</title>
<meta property="og:type" content="article"/>
<meta property="og:site_name" content="ST-Firm Akädemie"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="ST-Firm Akädemie — Learn · Build · Share · Empower."/>
<meta property="og:image" content="{image}"/>
<meta property="og:url" content="{share_url(request, gid)}"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:image" content="{image}"/>
<meta http-equiv="refresh" content="0; url={gallery_url}"/>
<script>location.replace("{gallery_url}");</script>
</head><body style="background:#080E30;color:#fff;font-family:sans-serif;text-align:center;padding:3rem">
Opening the ST-Firm Akädemie gallery… <a style="color:#C9A84C" href="{gallery_url}">Continue →</a>
</body></html>""")

# ── Routes: admin UI + health ───────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return FileResponse(STATIC / "admin.html")

@app.get("/")
def root():
    return RedirectResponse("/admin")

@app.get("/healthz")
def health():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}

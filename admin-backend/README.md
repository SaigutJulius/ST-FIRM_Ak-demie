# ST-Firm Akädemie — Admin Backend

A small, sovereign **FastAPI + SQLite** backend that lets you manage the website
gallery with a login: **upload photos, edit captions, delete stale images**, and
**monitor** activity (logins, uploads) — all from a browser dashboard at `/admin`.

The public site (GitHub Pages) reads the live gallery from this backend's
`/api/gallery`, and falls back to a baked-in set if the backend is asleep, so the
gallery never appears empty.

---

## Run locally

```bash
cd admin-backend
python -m venv .venv && .venv\Scripts\activate     # Windows
# source .venv/bin/activate                        # macOS/Linux
pip install -r requirements.txt
copy .env.example .env        # then edit ADMIN_USER / ADMIN_PASSWORD / JWT_SECRET
python seed.py                # creates admin user + imports current 12 photos
uvicorn main:app --env-file .env --port 8000
```

Open **http://localhost:8000/admin** and log in.

---

## Deploy (access from anywhere, incl. phone)

**Render (free):**
1. Push this repo to GitHub (already done).
2. On render.com → **New ▸ Blueprint** → pick this repo (it reads `render.yaml`).
3. Set `ADMIN_USER` and `ADMIN_PASSWORD` in the dashboard (secrets).
4. After the first deploy, copy your Render URL (e.g. `https://st-firm-akademie-admin.onrender.com`)
   and set it as `PUBLIC_BASE`, then redeploy.
5. In the **public site** `index.html`, set `const GALLERY_API = "https://…onrender.com"`.

> Free tier sleeps after ~15 min idle (first request wakes it in ~30 s). The public
> gallery shows the fallback instantly while it wakes, so visitors are never blocked.

---

## API

| Method | Route | Auth | Purpose |
|---|---|---|---|
| POST | `/api/login` | – | get a JWT token |
| GET | `/api/gallery` | – | list images (public) |
| POST | `/api/gallery` | ✅ | upload (auto-optimized) |
| PATCH | `/api/gallery/{id}` | ✅ | edit caption / order |
| DELETE | `/api/gallery/{id}` | ✅ | remove image |
| GET | `/api/stats` | ✅ | counts + activity log |
| GET | `/share/{id}` | – | social share preview page |

Captions support `**bold**` (two asterisks) for the bold part.

## Security notes
- Passwords are hashed (PBKDF2-SHA256, 200k iterations). `.env` and `data/` are git-ignored.
- Always set a strong `ADMIN_PASSWORD` and a random `JWT_SECRET` before deploying.

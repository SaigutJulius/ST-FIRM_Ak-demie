"""
Seed the admin DB: create the admin user (from .env) and import the
current 12 gallery images from ../index.html + ../gallery/ into the backend.
Run once:  python seed.py
"""
import os, re, shutil
from pathlib import Path

def load_env(p):
    p = Path(p)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env(Path(__file__).parent / ".env")
import main  # noqa: E402  (runs init_db with env loaded → seeds admin user)

REPO = Path(__file__).resolve().parent.parent
entries = []
try:
    idx = (REPO / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const GALLERY(?:_FALLBACK)? = \[(.*?)\];", idx, re.S)
    if m:
        entries = re.findall(r"\{\s*src:\s*'([^']+)',\s*cap:\s*'(.*?)'\s*\}", m.group(1))
    else:
        print("  (no fallback gallery found in index.html — starting empty)")
except Exception as e:
    print("  (could not read fallback gallery:", e, "— starting empty)")

def to_md(cap):
    cap = re.sub(r"<b>(.*?)</b>", r"**\1**", cap)
    return cap.replace("&amp;", "&")

c = main.db()
existing = c.execute("SELECT COUNT(*) AS n FROM gallery").fetchone()["n"]
if existing:
    print(f"Gallery already has {existing} item(s) — skipping image import.")
else:
    order = 1
    for src, cap in entries:
        sp = REPO / src
        if not sp.exists():
            print("  ! missing", src); continue
        shutil.copy(sp, main.UPLOADS / sp.name)
        c.execute("INSERT INTO gallery(filename, caption, sort_order, created_at) "
                  "VALUES(?,?,?,datetime('now'))", (sp.name, to_md(cap), order))
        print("  + imported", sp.name)
        order += 1
    c.commit()
c.close()

print("\nAdmin user:", main.ADMIN_USER, "(set)" if main.ADMIN_PASSWORD else "(WARNING: set ADMIN_PASSWORD in .env, then re-run)")
print("Done. Start the server:  uvicorn main:app --env-file .env --port 8000")

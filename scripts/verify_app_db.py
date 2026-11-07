import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import app.db as db

print("is_turso:", db.is_turso())
with db.db() as conn:
    row = conn.execute("SELECT COUNT(*) FROM patients").fetchone()
    print("Patient count via app.db:", row[0])
    p = conn.execute("SELECT code, name, phone FROM patients ORDER BY id DESC LIMIT 1").fetchone()
    print(f"Latest patient: {p['code']} - {p['name']} ({p['phone']})")

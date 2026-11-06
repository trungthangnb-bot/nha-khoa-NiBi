"""SQLite layer for Nha khoa NiBi - BS. Thắng."""
import contextlib
import os
import sqlite3

from .security import hash_secret
from .textutil import strip_accents

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("NIBI_DB") or os.path.join(BASE_DIR, "data", "nibi.db")

BRANCHES = ["Ninh Vân"]
CATEGORIES = ["Tổng quát", "Nhổ răng", "Điều trị tủy", "Răng giả", "Chỉnh nha", "Implant"]
METHODS = ["Tiền mặt", "Chuyển khoản"]
APPT_KINDS = ["hen_kham", "tai_kham"]
APPT_STATUSES = ["Chờ đến", "Đã đến", "Lỡ hẹn", "Hủy"]

DEFAULT_ADMIN_PASSWORD = "123456"
DEFAULT_EDIT_PIN = "0000"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','staff')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS sessions(
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS patients(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    name TEXT NOT NULL,
    search_text TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    phone_note TEXT NOT NULL DEFAULT '',
    yob INTEGER,
    address TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT 'Ninh Vân',
    medical_history TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_patients_branch ON patients(branch);

CREATE TABLE IF NOT EXISTS treatments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    visit_date TEXT,                         -- ISO yyyy-mm-dd, NULL for undated legacy rows
    branch TEXT NOT NULL,
    category TEXT NOT NULL,
    dentist TEXT NOT NULL DEFAULT '',
    procedure TEXT NOT NULL,
    tooth TEXT NOT NULL DEFAULT '',
    cost INTEGER NOT NULL DEFAULT 0,         -- VND
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'app',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_treat_patient ON treatments(patient_id);
CREATE INDEX IF NOT EXISTS ix_treat_date ON treatments(visit_date);

CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    treatment_id INTEGER NOT NULL REFERENCES treatments(id) ON DELETE CASCADE,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL CHECK(amount > 0),   -- VND
    method TEXT NOT NULL DEFAULT '',
    paid_on TEXT,                                -- ISO date, NULL for undated legacy rows
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_pay_treat ON payments(treatment_id);
CREATE INDEX IF NOT EXISTS ix_pay_date ON payments(paid_on);

CREATE TABLE IF NOT EXISTS appointments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('hen_kham','tai_kham')),
    appt_date TEXT NOT NULL,
    appt_time TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Chờ đến',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_appt_date ON appointments(appt_date);
CREATE INDEX IF NOT EXISTS ix_appt_patient ON appointments(patient_id);

CREATE TABLE IF NOT EXISTS audit_logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    username TEXT NOT NULL DEFAULT '',
    full_name TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    entity TEXT NOT NULL DEFAULT '',
    entity_id INTEGER,
    patient_id INTEGER,
    patient_name TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    changes TEXT NOT NULL DEFAULT '[]',
    is_read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_logs(at);
CREATE INDEX IF NOT EXISTS ix_audit_unread ON audit_logs(is_read, action);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextlib.contextmanager
def db():
    """One connection per request; commit on success, rollback on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def patient_search_text(name, phone, phone_note, address, code) -> str:
    return strip_accents(" ".join(x or "" for x in (name, phone, phone_note, address, code)))


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            conn.execute(
                "INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,'admin')",
                ("admin", hash_secret(DEFAULT_ADMIN_PASSWORD), "BS. Thắng (Quản trị)"),
            )
        if not conn.execute("SELECT 1 FROM settings WHERE key='edit_pin_hash'").fetchone():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('edit_pin_hash',?)",
                (hash_secret(DEFAULT_EDIT_PIN),),
            )
        conn.commit()
    finally:
        conn.close()

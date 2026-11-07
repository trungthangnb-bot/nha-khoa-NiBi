"""Nha khoa NiBi - BS. Thắng : FastAPI backend."""
import calendar
import contextlib
import csv
import datetime as dt
import io
import json
import os
import re
import sqlite3
import time
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db as dbm
from .db import APPT_KINDS, APPT_STATUSES, BRANCHES, CATEGORIES, METHODS, SUPPLY_CATEGORIES, SUPPLY_UNITS, db, patient_search_text
from .excel_exporter import generate_nibi_excel_workbook
from .ai_assistant import parse_intake_with_ai, answer_clinic_query, parse_supply_with_ai
from .security import Limiter, hash_secret, new_token, token_digest, verify_secret
from .textutil import clean, strip_accents

STATIC_DIR = os.path.join(dbm.BASE_DIR, "static")
login_limiter = Limiter(max_fails=6, window=300, lock_seconds=300)
login_user_limiter = Limiter(max_fails=40, window=900, lock_seconds=300)
pin_limiter = Limiter(max_fails=6, window=300, lock_seconds=300)
pw_limiter = Limiter(max_fails=6, window=300, lock_seconds=300)
_DUMMY_HASH = hash_secret("not-a-real-password")


@contextlib.asynccontextmanager
async def lifespan(_app):
    dbm.init_db()
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    yield


app = FastAPI(title="NiBi Dental", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_handler(_request, exc):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse({"detail": f"Dữ liệu không hợp lệ ({field or 'body'})"}, status_code=422)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# ───────────────────────── helpers ─────────────────────────
def today_iso() -> str:
    return dt.date.today().isoformat()


def vdate(s, label="Ngày", required=False):
    if s in (None, ""):
        if required:
            raise HTTPException(422, f"{label} không được để trống")
        return None
    try:
        return dt.date.fromisoformat(str(s)).isoformat()
    except ValueError:
        raise HTTPException(422, f"{label} không hợp lệ")


def vchoice(v, options, label):
    if v not in options:
        raise HTTPException(422, f"{label} không hợp lệ")
    return v


def vtime(s):
    s = (s or "").strip()
    if not s:
        return ""
    try:
        return dt.datetime.strptime(s, "%H:%M").strftime("%H:%M")
    except ValueError:
        raise HTTPException(422, "Giờ hẹn không hợp lệ")


def vyob(y):
    if y is None:
        return None
    if not 1900 <= y <= dt.date.today().year:
        raise HTTPException(422, "Năm sinh không hợp lệ")
    return y


def like_clause(q, column="p.search_text"):
    """Accent-insensitive, multi-word AND search."""
    sql, params = "", []
    for tok in strip_accents(q or "").split():
        esc = tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += f" AND {column} LIKE ? ESCAPE '\\'"
        params.append(f"%{esc}%")
    return sql, params


def patient_dict(row, with_age=True):
    d = dict(row)
    d.pop("search_text", None)
    if with_age:
        d["age"] = dt.date.today().year - d["yob"] if d.get("yob") else None
    return d


def audit(conn, user, action, entity="", entity_id=None, patient_id=None, patient_name="", summary="", changes=None):
    is_read = 1 if user.get("role") == "admin" else 0
    conn.execute(
        "INSERT INTO audit_logs(username,full_name,action,entity,entity_id,patient_id,patient_name,summary,changes,is_read)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user["username"], user["full_name"], action, entity, entity_id, patient_id, patient_name, summary,
         json.dumps(changes or [], ensure_ascii=False), is_read))


def diff(old, new, labels, money=()):
    out = []
    for field, label in labels.items():
        if field in new and (old.get(field) or "") != (new[field] or ""):
            out.append({"field": field, "label": label, "old": old.get(field), "new": new[field],
                        "money": field in money})
    return out


PATIENT_LABELS = {"name": "Họ tên", "phone": "SĐT", "yob": "Năm sinh", "address": "Địa chỉ", "branch": "Cơ sở",
                  "medical_history": "Tiền sử bệnh", "notes": "Ghi chú"}
TREATMENT_LABELS = {"visit_date": "Ngày khám", "branch": "Cơ sở", "category": "Nhóm dịch vụ", "dentist": "Bác sĩ",
                    "procedure": "Nội dung điều trị", "tooth": "Răng", "cost": "Tổng tiền", "notes": "Ghi chú"}


def money_text(v):
    v = int(v or 0)
    in_k = v / 1000
    if in_k == 0:
        return "0K"
    if in_k.is_integer():
        return f"{int(in_k):,}K".replace(",", ".")
    return f"{in_k:,.1f}K".replace(",", ".")


# ───────────────────────── auth ─────────────────────────
def current_user(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    nibi_token: Optional[str] = Cookie(None)
):
    raw_token = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()
    elif token:
        raw_token = token.strip()
    elif nibi_token:
        raw_token = nibi_token.strip()

    if not raw_token:
        raise HTTPException(401, "Chưa đăng nhập")
    digest = token_digest(raw_token)
    with db() as conn:
        row = conn.execute(
            "SELECT u.id,u.username,u.full_name,u.role FROM sessions s JOIN users u ON u.id=s.user_id"
            " WHERE s.token_hash=? AND s.expires_at>? AND u.active=1", (digest, time.time())).fetchone()
    if not row:
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn")
    user = dict(row)
    user["token_hash"] = digest
    return user


def admin_only(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Chỉ Admin mới có quyền này")
    return user


def check_pin(conn, user, pin):
    """Staff need the edit PIN; the admin (owner) is trusted but every change is still logged."""
    if user["role"] == "admin":
        return
    key = f"pin:{user['id']}"
    wait = pin_limiter.remaining(key)
    if wait:
        raise HTTPException(429, f"Nhập sai PIN quá nhiều lần, thử lại sau {wait} giây")
    stored = conn.execute("SELECT value FROM settings WHERE key='edit_pin_hash'").fetchone()
    if not pin or not verify_secret(str(pin), stored["value"]):
        pin_limiter.fail(key)
        raise HTTPException(403, "Mã PIN không đúng")
    pin_limiter.ok(key)


def check_admin_password(conn, user, password):
    key = f"pw:{user['id']}"
    wait = pw_limiter.remaining(key)
    if wait:
        raise HTTPException(429, f"Nhập sai mật khẩu quá nhiều lần, thử lại sau {wait} giây")
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
    if not password or not verify_secret(password, row["password_hash"]):
        pw_limiter.fail(key)
        raise HTTPException(403, "Mật khẩu không đúng")
    pw_limiter.ok(key)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


@app.post("/api/auth/login")
def login(data: LoginIn, request: Request):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    uname = data.username.strip().lower()
    k1, k2 = f"{ip}|{uname}", f"u|{uname}"
    wait = max(login_limiter.remaining(k1), login_user_limiter.remaining(k2))
    if wait:
        raise HTTPException(429, f"Đăng nhập sai quá nhiều lần, thử lại sau {wait} giây")
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (uname,)).fetchone()
        # Always run one hash so unknown usernames cost the same time as wrong passwords.
        ok = verify_secret(data.password, user["password_hash"] if user else _DUMMY_HASH) and bool(user)
        if not ok:
            login_limiter.fail(k1)
            login_user_limiter.fail(k2)
            raise HTTPException(401, "Sai tài khoản hoặc mật khẩu")
        login_limiter.ok(k1)
        token = new_token()
        ttl = 30 * 86400 if data.remember else 12 * 3600
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                     (token_digest(token), user["id"], time.time() + ttl))
    return {"token": token, "user": {"id": user["id"], "username": user["username"],
                                     "full_name": user["full_name"], "role": user["role"]}}


@app.post("/api/auth/logout")
def logout(user=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (user["token_hash"],))
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return {k: user[k] for k in ("id", "username", "full_name", "role")}


class ChangePwIn(BaseModel):
    old_password: str = Field(max_length=200)
    new_password: str = Field(min_length=6, max_length=200)


@app.post("/api/auth/change-password")
def change_password(data: ChangePwIn, user=Depends(current_user)):
    with db() as conn:
        check_admin_password(conn, user, data.old_password)
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_secret(data.new_password), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user["id"], user["token_hash"]))
        audit(conn, user, "password_change", "user", user["id"], summary="Đổi mật khẩu đăng nhập")
    return {"ok": True}


class PinIn(BaseModel):
    pin: str = Field(default="", max_length=20)


@app.post("/api/pin/verify")
def pin_verify(data: PinIn, user=Depends(current_user)):
    with db() as conn:
        check_pin(conn, user, data.pin)
    return {"ok": True}


class SetPinIn(BaseModel):
    password: str = Field(max_length=200)
    new_pin: str = Field(pattern=r"^\d{4}$")


@app.post("/api/admin/pin")
def set_pin(data: SetPinIn, user=Depends(admin_only)):
    with db() as conn:
        check_admin_password(conn, user, data.password)
        conn.execute("UPDATE settings SET value=? WHERE key='edit_pin_hash'", (hash_secret(data.new_pin),))
        audit(conn, user, "pin_change", summary="Đổi mã PIN chỉnh sửa của nhân viên")
    return {"ok": True}


@app.get("/api/meta")
def meta(user=Depends(current_user)):
    with db() as conn:
        def get_val(key, default):
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if r:
                try:
                    return json.loads(r["value"])
                except Exception:
                    return r["value"]
            return default

        clinic_name = get_val("clinic_name", "Nha Khoa NiBi - BS. Thắng")
        clinic_hotline = get_val("clinic_hotline", "0976.838.901")
        clinic_address = get_val("clinic_address", "Ninh Vân")
        branches = get_val("branches", BRANCHES)
        categories = get_val("categories", CATEGORIES)
        dentists = get_val("dentists", None)
        if not dentists:
            dentists = [r[0] for r in conn.execute(
                "SELECT DISTINCT dentist FROM treatments WHERE dentist<>'' ORDER BY dentist")]
            if "Thắng" not in dentists:
                dentists.insert(0, "Thắng")

        addresses = [r[0] for r in conn.execute(
            "SELECT address FROM patients WHERE address<>'' GROUP BY address ORDER BY COUNT(*) DESC, address"
        ).fetchall()]

    return {
        "clinic_name": clinic_name,
        "clinic_hotline": clinic_hotline,
        "clinic_address": clinic_address,
        "branches": branches,
        "categories": categories,
        "methods": METHODS,
        "statuses": APPT_STATUSES,
        "dentists": dentists,
        "addresses": addresses,
        "supply_categories": SUPPLY_CATEGORIES,
        "supply_units": SUPPLY_UNITS,
        "today": today_iso(),
    }


# ───────────────────────── patients ─────────────────────────
TP_CTE = """WITH tp AS (
    SELECT t.*, COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id=t.id),0) AS paid FROM treatments t
)"""


@app.get("/api/patients")
def list_patients(q: str = "", branch: str = "", only_debt: bool = False, limit: int = 60, offset: int = 0,
                  user=Depends(current_user)):
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    where, params = "WHERE 1=1", []
    if branch:
        where += " AND p.branch=?"
        params.append(branch)
    sql, p2 = like_clause(q)
    where, params = where + sql, params + p2
    having = "HAVING COALESCE(SUM(MAX(tp.cost-tp.paid,0)),0) > 0" if only_debt else ""
    inner = f"""{TP_CTE}
        SELECT p.*, COUNT(tp.id) AS visits, COALESCE(SUM(tp.cost),0) AS billed, COALESCE(SUM(tp.paid),0) AS paid,
               COALESCE(SUM(MAX(tp.cost-tp.paid,0)),0) AS debt, MAX(tp.visit_date) AS last_visit
        FROM patients p LEFT JOIN tp ON tp.patient_id=p.id {where} GROUP BY p.id {having}"""
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM ({inner})", params).fetchone()[0]
        rows = conn.execute(
            f"{inner} ORDER BY MAX(tp.visit_date) IS NULL, MAX(tp.visit_date) DESC, p.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
    return {"items": [patient_dict(r) for r in rows], "total": total}


class TreatmentIn(BaseModel):
    visit_date: Optional[str] = None
    branch: Optional[str] = None
    category: str = "Tổng quát"
    dentist: str = Field(default="", max_length=60)
    procedure: str = Field(min_length=1, max_length=300)
    tooth: str = Field(default="", max_length=60)
    cost: int = Field(default=0, ge=0, le=20_000_000_000)
    notes: str = Field(default="", max_length=2000)
    paid: int = Field(default=0, ge=0, le=20_000_000_000)
    method: str = "Tiền mặt"
    next_date: Optional[str] = None


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(default="", max_length=80)
    yob: Optional[int] = None
    address: str = Field(default="", max_length=200)
    branch: str = BRANCHES[0]
    medical_history: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=2000)


class PatientCreate(PatientIn):
    first_treatment: Optional[TreatmentIn] = None
    force: bool = False


class PatientUpdate(PatientIn):
    pin: Optional[str] = None


def add_calendar_months(sourcedate: dt.date, months: int) -> dt.date:
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def get_allowed_branches(conn=None) -> list[str]:
    def _read_from_conn(c):
        row = c.execute("SELECT value FROM settings WHERE key='branches'").fetchone()
        if row and row[0]:
            try:
                b_list = json.loads(row[0])
                if isinstance(b_list, list) and b_list:
                    return [str(b).strip() for b in b_list if str(b).strip()]
            except Exception:
                pass
        return None

    if conn is not None:
        res = _read_from_conn(conn)
        if res:
            return res
    else:
        with db() as c:
            res = _read_from_conn(c)
            if res:
                return res
    return BRANCHES


def insert_treatment(conn, user, pid, patient_name, branch_default, t: TreatmentIn):
    visit = vdate(t.visit_date, "Ngày khám") or today_iso()
    branch = vchoice(t.branch or branch_default, get_allowed_branches(conn), "Cơ sở")
    category = vchoice(t.category, CATEGORIES, "Nhóm dịch vụ")
    method = vchoice(t.method, METHODS, "Hình thức thanh toán")
    nxt = vdate(t.next_date, "Ngày hẹn tái khám")
    procedure = clean(t.procedure)
    if t.paid > t.cost:
        raise HTTPException(422, "Số tiền đã trả không được lớn hơn tổng tiền")
    cur = conn.execute(
        "INSERT INTO treatments(patient_id,visit_date,branch,category,dentist,procedure,tooth,cost,notes,created_by)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (pid, visit, branch, category, clean(t.dentist), procedure, clean(t.tooth), t.cost, t.notes.strip(),
         user["username"]))
    tid = cur.lastrowid
    if t.paid > 0:
        conn.execute("INSERT INTO payments(treatment_id,patient_id,amount,method,paid_on,created_by) VALUES(?,?,?,?,?,?)",
                     (tid, pid, t.paid, method, visit, user["username"]))

    # Clinical rules for automated recall appointment scheduling
    v_date_obj = dt.date.fromisoformat(visit)
    if category == "Răng giả":
        # Mặc định hẹn lần đầu sau 6 tháng, 1 năm, 3 năm và 5 năm
        d_6m = add_calendar_months(v_date_obj, 6).isoformat()
        d_1y = add_calendar_months(v_date_obj, 12).isoformat()
        d_3y = add_calendar_months(v_date_obj, 36).isoformat()
        d_5y = add_calendar_months(v_date_obj, 60).isoformat()
        first_appt = nxt if nxt else d_6m
        conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                     (pid, "tai_kham", first_appt, branch, f"Tái khám định kỳ Răng giả (sau 6 tháng): {procedure}", "Chờ đến"))
        conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                     (pid, "tai_kham", d_1y, branch, f"Tái khám định kỳ Răng giả (sau 1 năm): {procedure}", "Chờ đến"))
        conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                     (pid, "tai_kham", d_3y, branch, f"Tái khám định kỳ Răng giả (sau 3 năm): {procedure}", "Chờ đến"))
        conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                     (pid, "tai_kham", d_5y, branch, f"Tái khám định kỳ Răng giả (sau 5 năm): {procedure}", "Chờ đến"))
    elif category == "Chỉnh nha":
        # Mặc định hẹn 1 tháng 1 lần tới khi kết thúc hoàn toàn
        d_1m = add_calendar_months(v_date_obj, 1).isoformat()
        first_appt = nxt if nxt else d_1m
        conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                     (pid, "tai_kham", first_appt, branch, f"Tái khám Chỉnh nha định kỳ (1 tháng): {procedure}", "Chờ đến"))
    elif category == "Nhổ răng":
        # Hẹn tái khám sau 1 ngày để kiểm tra vết thương và hẹn tái khám sau 7 ngày hẹn cắt chỉ
        d_1d = (v_date_obj + dt.timedelta(days=1)).isoformat()
        d_7d = (v_date_obj + dt.timedelta(days=7)).isoformat()
        cut_stitch_date = nxt if nxt else d_7d
        conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                     (pid, "tai_kham", d_1d, branch, f"Kiểm tra vết thương (sau 1 ngày): {procedure}", "Chờ đến"))
        conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                     (pid, "tai_kham", cut_stitch_date, branch, f"Hẹn cắt chỉ (sau 7 ngày): {procedure}", "Chờ đến"))
    else:
        if nxt:
            conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                         (pid, "tai_kham", nxt, branch, f"Tái khám sau: {procedure}", "Chờ đến"))

    audit(conn, user, "treatment_create", "treatment", tid, pid, patient_name,
          f"Thêm đợt điều trị «{procedure}» - {money_text(t.cost)}, đã thu {money_text(t.paid)}")
    return tid


@app.post("/api/patients")
def create_patient(data: PatientCreate, user=Depends(current_user)):
    name = clean(data.name)
    if not name:
        raise HTTPException(422, "Họ tên không được để trống")
    yob = vyob(data.yob)
    phone = clean(data.phone)
    with db() as conn:
        vchoice(data.branch, get_allowed_branches(conn), "Cơ sở")
        if not data.force:
            for r in conn.execute("SELECT id,name,phone,yob,address FROM patients WHERE search_text LIKE ?",
                                  (f"%{strip_accents(name)}%",)):
                if strip_accents(r["name"]) == strip_accents(name) and (
                        (phone and r["phone"] == phone) or (yob and r["yob"] == yob)):
                    raise HTTPException(409, {"message": "Đã có bệnh nhân trùng tên và năm sinh/SĐT",
                                              "existing_id": r["id"]})
        cur = conn.execute(
            "INSERT INTO patients(name,phone,yob,address,branch,medical_history,notes) VALUES(?,?,?,?,?,?,?)",
            (name, phone, yob, clean(data.address), data.branch, data.medical_history.strip(), data.notes.strip()))
        pid = cur.lastrowid
        code = f"BN{pid:05d}"
        conn.execute("UPDATE patients SET code=?, search_text=? WHERE id=?",
                     (code, patient_search_text(name, phone, "", clean(data.address), code), pid))
        audit(conn, user, "patient_create", "patient", pid, pid, name, f"Tiếp đón bệnh nhân mới {name} ({code})")
        tid = None
        if data.first_treatment:
            tid = insert_treatment(conn, user, pid, name, data.branch, data.first_treatment)
    return {"id": pid, "code": code, "treatment_id": tid}


def load_patient_detail(conn, pid):
    p = conn.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404, "Không tìm thấy bệnh nhân")
    treatments = [dict(r) for r in conn.execute(
        "SELECT * FROM treatments WHERE patient_id=? ORDER BY visit_date IS NULL, visit_date DESC, id DESC", (pid,))]
    pays = {}
    for r in conn.execute("SELECT * FROM payments WHERE patient_id=? ORDER BY paid_on IS NULL, paid_on, id", (pid,)):
        pays.setdefault(r["treatment_id"], []).append(dict(r))
    billed = paid = debt = 0
    for t in treatments:
        t["payments"] = pays.get(t["id"], [])
        t["paid"] = sum(x["amount"] for x in t["payments"])
        t["debt"] = max(t["cost"] - t["paid"], 0)
        billed, paid, debt = billed + t["cost"], paid + t["paid"], debt + t["debt"]
    appts = [dict(r) for r in conn.execute(
        "SELECT * FROM appointments WHERE patient_id=? ORDER BY appt_date DESC, id DESC", (pid,))]
    return {"patient": patient_dict(p), "treatments": treatments, "appointments": appts,
            "totals": {"billed": billed, "paid": paid, "debt": debt}}


@app.get("/api/patients/{pid}")
def get_patient(pid: int, user=Depends(current_user)):
    with db() as conn:
        return load_patient_detail(conn, pid)


@app.put("/api/patients/{pid}")
def update_patient(pid: int, data: PatientUpdate, user=Depends(current_user)):
    name = clean(data.name)
    if not name:
        raise HTTPException(422, "Họ tên không được để trống")
    with db() as conn:
        vchoice(data.branch, get_allowed_branches(conn), "Cơ sở")
        check_pin(conn, user, data.pin)
        old = conn.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
        if not old:
            raise HTTPException(404, "Không tìm thấy bệnh nhân")
        new = {"name": name, "phone": clean(data.phone), "yob": vyob(data.yob), "address": clean(data.address),
               "branch": data.branch, "medical_history": data.medical_history.strip(), "notes": data.notes.strip()}
        changes = diff(dict(old), new, PATIENT_LABELS)
        if changes:
            conn.execute(
                "UPDATE patients SET name=?,phone=?,yob=?,address=?,branch=?,medical_history=?,notes=?,"
                "search_text=?,updated_at=datetime('now','localtime') WHERE id=?",
                (new["name"], new["phone"], new["yob"], new["address"], new["branch"], new["medical_history"],
                 new["notes"], patient_search_text(new["name"], new["phone"], old["phone_note"], new["address"],
                                                   old["code"]), pid))
            audit(conn, user, "patient_edit", "patient", pid, pid, name,
                  "Sửa thông tin: " + ", ".join(c["label"] for c in changes), changes)
    return {"ok": True, "changed": len(changes)}


class DeletePatientIn(BaseModel):
    password: str = Field(max_length=200)


@app.post("/api/patients/{pid}/delete")
def delete_patient(pid: int, data: DeletePatientIn, user=Depends(admin_only)):
    with db() as conn:
        check_admin_password(conn, user, data.password)
        detail = load_patient_detail(conn, pid)
        p = detail["patient"]
        snapshot = [{"field": "Hồ sơ đã xóa", "label": "Hồ sơ đã xóa", "old": json.dumps(
            {"patient": p, "treatments": len(detail["treatments"]), "totals": detail["totals"]},
            ensure_ascii=False), "new": None}]
        conn.execute("DELETE FROM patients WHERE id=?", (pid,))
        audit(conn, user, "patient_delete", "patient", pid, pid, p["name"],
              f"XÓA hồ sơ {p['name']} ({p['code']}): {len(detail['treatments'])} đợt điều trị, "
              f"đã thu {money_text(detail['totals']['paid'])}, còn nợ {money_text(detail['totals']['debt'])}", snapshot)
    return {"ok": True}


# ───────────────────────── treatments & payments ─────────────────────────
@app.post("/api/patients/{pid}/treatments")
def add_treatment(pid: int, data: TreatmentIn, user=Depends(current_user)):
    with db() as conn:
        p = conn.execute("SELECT id,name,branch FROM patients WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Không tìm thấy bệnh nhân")
        tid = insert_treatment(conn, user, pid, p["name"], p["branch"], data)
    return {"id": tid}


class TreatmentUpdate(BaseModel):
    visit_date: Optional[str] = None
    branch: str
    category: str
    dentist: str = Field(default="", max_length=60)
    procedure: str = Field(min_length=1, max_length=300)
    tooth: str = Field(default="", max_length=60)
    cost: int = Field(ge=0, le=20_000_000_000)
    notes: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=300)
    pin: Optional[str] = None


def treatment_paid(conn, tid):
    return conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE treatment_id=?", (tid,)).fetchone()[0]


@app.put("/api/treatments/{tid}")
def update_treatment(tid: int, data: TreatmentUpdate, user=Depends(current_user)):
    with db() as conn:
        check_pin(conn, user, data.pin)
        old = conn.execute("SELECT t.*, p.name AS pname FROM treatments t JOIN patients p ON p.id=t.patient_id "
                           "WHERE t.id=?", (tid,)).fetchone()
        if not old:
            raise HTTPException(404, "Không tìm thấy đợt điều trị")
        new = {"visit_date": vdate(data.visit_date, "Ngày khám"), "branch": vchoice(data.branch, get_allowed_branches(conn), "Cơ sở"),
               "category": vchoice(data.category, CATEGORIES, "Nhóm dịch vụ"), "dentist": clean(data.dentist),
               "procedure": clean(data.procedure), "tooth": clean(data.tooth), "cost": data.cost,
               "notes": data.notes.strip()}
        paid = treatment_paid(conn, tid)
        if new["cost"] < paid:
            raise HTTPException(422, f"Tổng tiền ({money_text(new['cost'])}) thấp hơn số đã thu "
                                     f"({money_text(paid)}). Hãy xóa khoản thu sai trước.")
        changes = diff(dict(old), {k: (v if v is not None else "") for k, v in new.items()},
                       TREATMENT_LABELS, money=("cost",))
        if changes:
            conn.execute(
                "UPDATE treatments SET visit_date=?,branch=?,category=?,dentist=?,procedure=?,tooth=?,cost=?,notes=?,"
                "updated_at=datetime('now','localtime') WHERE id=?",
                (new["visit_date"], new["branch"], new["category"], new["dentist"], new["procedure"], new["tooth"],
                 new["cost"], new["notes"], tid))
            summary = f"Sửa đợt điều trị «{old['procedure']}»: " + ", ".join(c["label"] for c in changes)
            if data.reason.strip():
                summary += f" — Lý do: {data.reason.strip()}"
            audit(conn, user, "treatment_edit", "treatment", tid, old["patient_id"], old["pname"], summary, changes)
    return {"ok": True, "changed": len(changes)}


class PinOnly(BaseModel):
    pin: Optional[str] = None
    reason: str = Field(default="", max_length=300)


@app.post("/api/treatments/{tid}/delete")
def delete_treatment(tid: int, data: PinOnly, user=Depends(current_user)):
    with db() as conn:
        check_pin(conn, user, data.pin)
        old = conn.execute("SELECT t.*, p.name AS pname FROM treatments t JOIN patients p ON p.id=t.patient_id "
                           "WHERE t.id=?", (tid,)).fetchone()
        if not old:
            raise HTTPException(404, "Không tìm thấy đợt điều trị")
        paid = treatment_paid(conn, tid)
        conn.execute("DELETE FROM treatments WHERE id=?", (tid,))
        audit(conn, user, "treatment_delete", "treatment", tid, old["patient_id"], old["pname"],
              f"XÓA đợt điều trị «{old['procedure']}» ({old['visit_date'] or 'không ngày'}) - tổng "
              f"{money_text(old['cost'])}, đã thu {money_text(paid)}",
              [{"field": "cost", "label": "Tổng tiền", "old": old["cost"], "new": None, "money": True}])
    return {"ok": True}


class PaymentIn(BaseModel):
    amount: int = Field(gt=0, le=20_000_000_000)
    method: str = "Tiền mặt"
    paid_on: Optional[str] = None
    note: str = Field(default="", max_length=200)


def add_payment(conn, user, t_row, amount, method, paid_on, note):
    conn.execute("INSERT INTO payments(treatment_id,patient_id,amount,method,paid_on,note,created_by) VALUES(?,?,?,?,?,?,?)",
                 (t_row["id"], t_row["patient_id"], amount, method, paid_on, note, user["username"]))


@app.post("/api/treatments/{tid}/payments")
def pay_treatment(tid: int, data: PaymentIn, user=Depends(current_user)):
    method = vchoice(data.method, METHODS, "Hình thức thanh toán")
    paid_on = vdate(data.paid_on, "Ngày thu") or today_iso()
    with db() as conn:
        t = conn.execute("SELECT t.*, p.name AS pname FROM treatments t JOIN patients p ON p.id=t.patient_id "
                         "WHERE t.id=?", (tid,)).fetchone()
        if not t:
            raise HTTPException(404, "Không tìm thấy đợt điều trị")
        debt = t["cost"] - treatment_paid(conn, tid)
        if data.amount > debt:
            raise HTTPException(422, f"Số tiền thu ({money_text(data.amount)}) lớn hơn số còn nợ ({money_text(max(debt, 0))})")
        add_payment(conn, user, t, data.amount, method, paid_on, clean(data.note))
        audit(conn, user, "payment", "treatment", tid, t["patient_id"], t["pname"],
              f"Thu {money_text(data.amount)} ({method}) cho «{t['procedure']}»")
    return {"ok": True}


@app.post("/api/payments/{payment_id}/delete")
def delete_payment(payment_id: int, data: PinOnly, user=Depends(current_user)):
    with db() as conn:
        check_pin(conn, user, data.pin)
        r = conn.execute("SELECT pay.*, p.name AS pname, t.procedure FROM payments pay "
                         "JOIN patients p ON p.id=pay.patient_id JOIN treatments t ON t.id=pay.treatment_id "
                         "WHERE pay.id=?", (payment_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Không tìm thấy khoản thu")
        conn.execute("DELETE FROM payments WHERE id=?", (payment_id,))
        audit(conn, user, "payment_delete", "payment", payment_id, r["patient_id"], r["pname"],
              f"XÓA khoản thu {money_text(r['amount'])} ngày {r['paid_on'] or 'không ngày'} của «{r['procedure']}»",
              [{"field": "amount", "label": "Khoản thu", "old": r["amount"], "new": None, "money": True}])
    return {"ok": True}


class CollectIn(BaseModel):
    amount: int = Field(gt=0, le=20_000_000_000)
    method: str = "Tiền mặt"
    note: str = Field(default="", max_length=200)


@app.post("/api/patients/{pid}/collect")
def collect_debt(pid: int, data: CollectIn, user=Depends(current_user)):
    """Thu nợ: money is applied to the oldest unpaid treatments first."""
    method = vchoice(data.method, METHODS, "Hình thức thanh toán")
    with db() as conn:
        p = conn.execute("SELECT id,name FROM patients WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Không tìm thấy bệnh nhân")
        rows = conn.execute(f"{TP_CTE} SELECT * FROM tp WHERE patient_id=? AND cost-paid>0 "
                            "ORDER BY visit_date IS NOT NULL, visit_date, id", (pid,)).fetchall()
        total_debt = sum(r["cost"] - r["paid"] for r in rows)
        if data.amount > total_debt:
            raise HTTPException(422, f"Số tiền thu ({money_text(data.amount)}) lớn hơn tổng nợ ({money_text(total_debt)})")
        left, parts = data.amount, []
        for r in rows:
            if left <= 0:
                break
            take = min(left, r["cost"] - r["paid"])
            add_payment(conn, user, r, take, method, today_iso(), clean(data.note))
            parts.append({"treatment_id": r["id"], "procedure": r["procedure"], "amount": take})
            left -= take
        audit(conn, user, "payment", "patient", pid, pid, p["name"],
              f"Thu nợ {money_text(data.amount)} ({method}) — còn nợ {money_text(total_debt - data.amount)}")
    return {"ok": True, "applied": parts, "remaining_debt": total_debt - data.amount}


# ───────────────────────── debts ─────────────────────────
def debt_groups(conn, branch="", q="", sort="debt"):
    where, params = "WHERE tp.cost-tp.paid>0", []
    if branch:
        where += " AND tp.branch=?"
        params.append(branch)
    sql, p2 = like_clause(q)
    rows = conn.execute(
        f"""{TP_CTE} SELECT p.id AS pid, p.name, p.code, p.phone, p.phone_note, p.address, p.branch AS pbranch,
               tp.id AS tid, tp.procedure, tp.category, tp.visit_date, tp.branch, tp.cost, tp.paid,
               tp.cost-tp.paid AS debt
            FROM tp JOIN patients p ON p.id=tp.patient_id {where}{sql}""", params + p2).fetchall()
    groups = {}
    for r in rows:
        g = groups.setdefault(r["pid"], {"patient_id": r["pid"], "name": r["name"], "code": r["code"],
                                         "phone": r["phone"], "phone_note": r["phone_note"], "address": r["address"],
                                         "branch": r["pbranch"], "debt": 0, "oldest": None, "treatments": []})
        g["debt"] += r["debt"]
        if r["visit_date"] and (not g["oldest"] or r["visit_date"] < g["oldest"]):
            g["oldest"] = r["visit_date"]
        g["treatments"].append({k: r[k] for k in ("tid", "procedure", "category", "visit_date", "branch", "cost", "paid", "debt")})
    items = list(groups.values())
    if sort == "oldest":
        items.sort(key=lambda g: (g["oldest"] is None, g["oldest"] or "", -g["debt"]))
    else:
        items.sort(key=lambda g: -g["debt"])
    return items


@app.get("/api/debts")
def list_debts(branch: str = "", q: str = "", sort: str = "debt", limit: int = 60, offset: int = 0,
               user=Depends(current_user)):
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    with db() as conn:
        items = debt_groups(conn, branch, q, sort)
    return {"items": items[offset:offset + limit], "total": len(items),
            "totals": {"debt": sum(g["debt"] for g in items), "patients": len(items)}}


def csv_response(rows, filename):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export/debts.csv")
def export_debts(branch: str = "", user=Depends(current_user)):
    with db() as conn:
        groups = debt_groups(conn, branch)
    rows = [["Mã BN", "Họ tên", "SĐT", "Địa chỉ", "Cơ sở", "Còn nợ (đồng)", "Nợ từ ngày", "Chi tiết"]]
    for g in groups:
        detail = "; ".join(f"{t['procedure']}: {t['debt']}" for t in g["treatments"])
        rows.append([g["code"], g["name"], g["phone"], g["address"], g["branch"], g["debt"], g["oldest"] or "", detail])
    return csv_response(rows, f"cong-no-{today_iso()}.csv")


@app.get("/api/export/patients.csv")
def export_patients(user=Depends(admin_only)):
    with db() as conn:
        rows = conn.execute(f"""{TP_CTE} SELECT p.code,p.name,p.phone,p.yob,p.address,p.branch,p.medical_history,
            COUNT(tp.id) AS visits, COALESCE(SUM(tp.cost),0) AS billed, COALESCE(SUM(tp.paid),0) AS paid,
            COALESCE(SUM(MAX(tp.cost-tp.paid,0)),0) AS debt FROM patients p LEFT JOIN tp ON tp.patient_id=p.id
            GROUP BY p.id ORDER BY p.id""").fetchall()
    out = [["Mã BN", "Họ tên", "SĐT", "Năm sinh", "Địa chỉ", "Cơ sở", "Tiền sử", "Số đợt ĐT", "Tổng tiền", "Đã thu", "Còn nợ"]]
    out += [list(r) for r in rows]
    return csv_response(out, f"benh-nhan-{today_iso()}.csv")


# ───────────────────────── appointments ─────────────────────────
class ApptIn(BaseModel):
    patient_id: int
    kind: str = "hen_kham"
    appt_date: str
    appt_time: str = ""
    branch: Optional[str] = None
    reason: str = Field(default="", max_length=300)


class ApptUpdate(BaseModel):
    status: Optional[str] = None
    appt_date: Optional[str] = None
    appt_time: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=300)


@app.get("/api/appointments")
def list_appointments(kind: str = "", when: str = "month", branch: str = "", q: str = "", month: str = "", limit: int = 150,
                      user=Depends(current_user)):
    today = today_iso()
    cur_month = month or today[:7]
    where, params = "WHERE 1=1", []
    if kind:
        vchoice(kind, APPT_KINDS, "Loại lịch")
        where += " AND a.kind=?"
        params.append(kind)
    if branch:
        where += " AND a.branch=?"
        params.append(branch)
    sql, p2 = like_clause(q)
    where, params = where + sql, params + p2
    conds = {
        "month": ("AND substr(a.appt_date,1,7)=?", [cur_month], "a.appt_date, a.appt_time, a.id"),
        "today": ("AND a.appt_date=?", [today], "a.appt_time, a.id"),
        "upcoming": ("AND a.appt_date>=? AND a.status='Chờ đến'", [today], "a.appt_date, a.appt_time, a.id"),
        "overdue": ("AND a.appt_date<? AND a.status='Chờ đến'", [today], "a.appt_date DESC, a.id"),
        "done": ("AND a.status<>'Chờ đến'", [], "a.appt_date DESC, a.id DESC"),
        "all": ("", [], "a.appt_date DESC, a.id DESC"),
    }
    if when not in conds:
        raise HTTPException(422, "Bộ lọc không hợp lệ")
    extra, ep, order = conds[when]
    base = f"""FROM appointments a JOIN patients p ON p.id=a.patient_id {where}"""
    with db() as conn:
        rows = conn.execute(
            f"SELECT a.*, p.name AS patient_name, p.code AS patient_code, p.phone AS patient_phone, "
            f"p.phone_note AS patient_phone_note {base} {extra} ORDER BY {order} LIMIT ?",
            params + ep + [max(1, min(limit, 300))]).fetchall()

        def count(cond, cp):
            return conn.execute(f"SELECT COUNT(*) {base} {cond}", params + cp).fetchone()[0]
        counts = {
            "month": count("AND substr(a.appt_date,1,7)=?", [cur_month]),
            "today": count("AND a.appt_date=?", [today]),
            "upcoming": count("AND a.appt_date>=? AND a.status='Chờ đến'", [today]),
            "overdue": count("AND a.appt_date<? AND a.status='Chờ đến'", [today])
        }
    return {"items": [dict(r) for r in rows], "counts": counts, "current_month": cur_month}


@app.post("/api/appointments")
def create_appointment(data: ApptIn, user=Depends(current_user)):
    vchoice(data.kind, APPT_KINDS, "Loại lịch")
    date = vdate(data.appt_date, "Ngày hẹn", required=True)
    with db() as conn:
        p = conn.execute("SELECT id,name,branch FROM patients WHERE id=?", (data.patient_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Không tìm thấy bệnh nhân")
        branch = vchoice(data.branch or p["branch"], get_allowed_branches(conn), "Cơ sở")
        cur = conn.execute("INSERT INTO appointments(patient_id,kind,appt_date,appt_time,branch,reason) VALUES(?,?,?,?,?,?)",
                           (p["id"], data.kind, date, vtime(data.appt_time), branch, clean(data.reason)))
        audit(conn, user, "appointment_create", "appointment", cur.lastrowid, p["id"], p["name"],
              f"Hẹn {'tái khám' if data.kind == 'tai_kham' else 'khám'} ngày {date}")
    return {"id": cur.lastrowid}


@app.put("/api/appointments/{aid}")
def update_appointment(aid: int, data: ApptUpdate, user=Depends(current_user)):
    sets, params = [], []
    if data.status is not None:
        sets.append("status=?")
        params.append(vchoice(data.status, APPT_STATUSES, "Trạng thái"))
    if data.appt_date is not None:
        sets.append("appt_date=?")
        params.append(vdate(data.appt_date, "Ngày hẹn", required=True))
    if data.appt_time is not None:
        sets.append("appt_time=?")
        params.append(vtime(data.appt_time))
    if data.reason is not None:
        sets.append("reason=?")
        params.append(clean(data.reason))
    if not sets:
        return {"ok": True}
    with db() as conn:
        cur_appt = conn.execute("SELECT * FROM appointments WHERE id=?", (aid,)).fetchone()
        if not cur_appt:
            raise HTTPException(404, "Không tìm thấy lịch hẹn")
        conn.execute(f"UPDATE appointments SET {', '.join(sets)} WHERE id=?", params + [aid])

        # If orthodontic appointment is marked 'Đã đến', auto queue the next 1-month follow up
        if data.status == "Đã đến" and "Chỉnh nha" in (cur_appt["reason"] or ""):
            cur_date = dt.date.fromisoformat(cur_appt["appt_date"])
            next_m = add_calendar_months(cur_date, 1).isoformat()
            have = conn.execute("SELECT 1 FROM appointments WHERE patient_id=? AND appt_date=? AND status='Chờ đến'",
                                (cur_appt["patient_id"], next_m)).fetchone()
            if not have:
                conn.execute(
                    "INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                    (cur_appt["patient_id"], "tai_kham", next_m, cur_appt["branch"],
                     "Tái khám Chỉnh nha định kỳ (1 tháng tiếp theo)", "Chờ đến")
                )

        # If denture appointment is marked 'Đã đến', auto queue the next 1-year follow up (những lần sau là 1 năm)
        if data.status == "Đã đến" and "Răng giả" in (cur_appt["reason"] or ""):
            cur_date = dt.date.fromisoformat(cur_appt["appt_date"])
            next_1y = add_calendar_months(cur_date, 12).isoformat()
            have = conn.execute("SELECT 1 FROM appointments WHERE patient_id=? AND appt_date=? AND status='Chờ đến'",
                                (cur_appt["patient_id"], next_1y)).fetchone()
            if not have:
                conn.execute(
                    "INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                    (cur_appt["patient_id"], "tai_kham", next_1y, cur_appt["branch"],
                     "Tái khám định kỳ Răng giả (sau 1 năm tiếp theo)", "Chờ đến")
                )
    return {"ok": True}


@app.delete("/api/appointments/{aid}")
def delete_appointment(aid: int, user=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM appointments WHERE id=?", (aid,))
    return {"ok": True}


# ───────────────────────── dashboard & reports ─────────────────────────
def month_keys(n=12):
    d = dt.date.today().replace(day=1)
    keys = []
    for _ in range(n):
        keys.append(d.strftime("%Y-%m"))
        d = (d - dt.timedelta(days=1)).replace(day=1)
    return keys[::-1]


def revenue_blocks(conn, branch, since, until):
    """Return collected (payments) and billed (treatment cost) grouped by month between ISO dates."""
    bw, bp = ("AND t.branch=?", [branch]) if branch else ("", [])
    collected = {r[0]: r[1] for r in conn.execute(
        f"SELECT substr(pay.paid_on,1,7), SUM(pay.amount) FROM payments pay JOIN treatments t ON t.id=pay.treatment_id "
        f"WHERE pay.paid_on BETWEEN ? AND ? {bw} GROUP BY 1", [since, until] + bp)}
    billed = {r[0]: (r[1], r[2]) for r in conn.execute(
        f"SELECT substr(t.visit_date,1,7), SUM(t.cost), COUNT(*) FROM treatments t "
        f"WHERE t.visit_date BETWEEN ? AND ? {bw} GROUP BY 1", [since, until] + bp)}
    return collected, billed


@app.get("/api/dashboard")
def dashboard(branch: str = "", user=Depends(admin_only)):
    today = dt.date.today()
    iso, ym = today.isoformat(), today.strftime("%Y-%m")
    bw, bp = ("AND t.branch=?", [branch]) if branch else ("", [])
    with db() as conn:
        patients = conn.execute("SELECT COUNT(*) FROM patients" + (" WHERE branch=?" if branch else ""),
                                [branch] if branch else []).fetchone()[0]
        visits_today = conn.execute(f"SELECT COUNT(*) FROM treatments t WHERE t.visit_date=? {bw}", [iso] + bp).fetchone()[0]
        p_bw, p_bp = ("AND branch=?", [branch]) if branch else ("", [])
        new_p_today = conn.execute(
            f"SELECT COUNT(*) FROM patients WHERE substr(created_at,1,10)=? "
            f"AND NOT EXISTS (SELECT 1 FROM treatments WHERE patient_id=patients.id AND visit_date=?) {p_bw}",
            [iso, iso] + p_bp
        ).fetchone()[0]
        visits_today += new_p_today
        visits_month = conn.execute(f"SELECT COUNT(*) FROM treatments t WHERE substr(t.visit_date,1,7)=? {bw}",
                                    [ym] + bp).fetchone()[0]
        groups = debt_groups(conn, branch)
        a_where, a_params = ("AND a.branch=?", [branch]) if branch else ("", [])
        appt_sql = ("SELECT a.*, p.name AS patient_name, p.phone AS patient_phone FROM appointments a "
                    "JOIN patients p ON p.id=a.patient_id WHERE a.status='Chờ đến' ")
        upcoming = [dict(r) for r in conn.execute(
            appt_sql + f"AND a.appt_date BETWEEN ? AND ? {a_where} ORDER BY a.appt_date, a.appt_time LIMIT 8",
            [iso, (today + dt.timedelta(days=7)).isoformat()] + a_params)]
        overdue = conn.execute(
            f"SELECT COUNT(*) FROM appointments a WHERE a.status='Chờ đến' AND a.appt_date<? {a_where}",
            [iso] + a_params).fetchone()[0]
        out = {"patients": patients, "visits_today": visits_today, "visits_month": visits_month,
               "debt": {"total": sum(g["debt"] for g in groups), "patients": len(groups), "top": [
                   {k: g[k] for k in ("patient_id", "name", "phone", "debt", "oldest")} for g in groups[:6]]},
               "upcoming": upcoming, "overdue": overdue, "revenue": None}
        if user["role"] == "admin":
            keys = month_keys(12)
            collected, billed = revenue_blocks(conn, branch, keys[0] + "-01", iso)
            year = str(today.year)
            cat = [dict(r) for r in conn.execute(
                f"SELECT t.category AS name, SUM(t.cost) AS billed, COUNT(*) AS visits FROM treatments t "
                f"WHERE substr(t.visit_date,1,4)=? {bw} GROUP BY 1 ORDER BY 2 DESC", [year] + bp)]
            by_branch = [dict(r) for r in conn.execute(
                "SELECT t.branch AS name, SUM(t.cost) AS billed FROM treatments t "
                "WHERE substr(t.visit_date,1,4)=? GROUP BY 1 ORDER BY 2 DESC", [year])]
            pay_today = conn.execute(
                f"SELECT COALESCE(SUM(pay.amount),0) FROM payments pay JOIN treatments t ON t.id=pay.treatment_id "
                f"WHERE pay.paid_on=? {bw}", [iso] + bp).fetchone()[0]
            out["revenue"] = {
                "today": pay_today, "month": collected.get(ym, 0), "billed_month": (billed.get(ym) or (0, 0))[0],
                "year": sum(v for k, v in collected.items() if k.startswith(year)),
                "series": [{"month": k, "collected": collected.get(k, 0), "billed": (billed.get(k) or (0, 0))[0]}
                           for k in keys],
                "categories": cat, "branches": by_branch}
    return out


@app.get("/api/admin/today-visits")
def today_visits(date: Optional[str] = None, branch: str = "", user=Depends(current_user)):
    """Trả về danh sách chi tiết bệnh nhân đến khám trong ngày cho Admin đối soát cuối buổi."""
    target_date = vdate(date, "Ngày xem") if date else dt.date.today().isoformat()
    bw, bp = ("AND t.branch=?", [branch]) if branch else ("", [])
    p_bw, p_bp = ("AND p.branch=?", [branch]) if branch else ("", [])
    is_adm = (user["role"] == "admin")

    with db() as conn:
        # 1. Các đợt điều trị / thủ thuật thực hiện trong ngày
        treatments_sql = f"""
            SELECT 
                t.id, t.patient_id, t.branch, t.category, t.dentist, t.procedure, t.tooth, t.cost, t.notes, t.visit_date,
                p.code AS patient_code, p.name AS patient_name, p.phone AS patient_phone, p.yob AS patient_yob,
                p.address AS patient_address,
                COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id=t.id), 0) AS paid,
                COALESCE((SELECT GROUP_CONCAT(DISTINCT method) FROM payments WHERE treatment_id=t.id), 'Tiền mặt') AS method
            FROM treatments t
            JOIN patients p ON p.id = t.patient_id
            WHERE t.visit_date = ? {bw}
            ORDER BY t.id DESC
        """
        t_rows = [dict(r) for r in conn.execute(treatments_sql, [target_date] + bp).fetchall()]

        # 2. Các bệnh nhân mới tiếp đón trong ngày nhưng chưa có thủ thuật
        patients_sql = f"""
            SELECT 
                0 AS id, p.id AS patient_id, p.branch, 'Khám tư vấn' AS category, 'Thắng' AS dentist,
                'Tiếp đón & tư vấn (chưa thực hiện thủ thuật)' AS procedure, '' AS tooth, 0 AS cost, p.notes,
                substr(p.created_at, 1, 10) AS visit_date,
                p.code AS patient_code, p.name AS patient_name, p.phone AS patient_phone, p.yob AS patient_yob,
                p.address AS patient_address,
                0 AS paid, 'Tiền mặt' AS method
            FROM patients p
            WHERE substr(p.created_at, 1, 10) = ? {p_bw}
              AND NOT EXISTS (SELECT 1 FROM treatments t WHERE t.patient_id = p.id AND t.visit_date = ?)
            ORDER BY p.id DESC
        """
        p_rows = [dict(r) for r in conn.execute(patients_sql, [target_date] + p_bp + [target_date]).fetchall()]

        all_items = t_rows + p_rows

        total_billed = sum(item["cost"] for item in all_items)
        total_paid = sum(item["paid"] for item in all_items)
        total_debt = sum(max(item["cost"] - item["paid"], 0) for item in all_items)
        unique_patients = len(set(item["patient_id"] for item in all_items))

        # Tiền mặt vs Chuyển khoản trong ngày
        pay_sql = f"""
            SELECT 
                COALESCE(SUM(CASE WHEN pay.method LIKE '%Tiền mặt%' THEN pay.amount ELSE 0 END), 0) AS cash,
                COALESCE(SUM(CASE WHEN pay.method LIKE '%Chuyển khoản%' THEN pay.amount ELSE 0 END), 0) AS transfer
            FROM payments pay
            JOIN treatments t ON t.id = pay.treatment_id
            WHERE pay.paid_on = ? {bw}
        """
        pay_row = conn.execute(pay_sql, [target_date] + bp).fetchone()
        cash_paid = pay_row[0] if pay_row else 0
        transfer_paid = pay_row[1] if pay_row else 0

        # Nếu không phải admin, ẩn số liệu tài chính để bảo mật RBAC
        if not is_adm:
            for it in all_items:
                it["cost"] = 0
                it["paid"] = 0
            total_billed = 0
            total_paid = 0
            cash_paid = 0
            transfer_paid = 0
            total_debt = 0

    return {
        "date": target_date,
        "branch": branch,
        "is_admin": is_adm,
        "items": all_items,
        "summary": {
            "total_visits": len(all_items),
            "unique_patients": unique_patients,
            "total_billed": total_billed,
            "total_paid": total_paid,
            "cash_paid": cash_paid,
            "transfer_paid": transfer_paid,
            "total_debt": total_debt
        }
    }


@app.get("/api/admin/reports")
def reports(year: int = 0, branch: str = "", user=Depends(admin_only)):
    year = year or dt.date.today().year
    bw, bp = ("AND t.branch=?", [branch]) if branch else ("", [])
    with db() as conn:
        collected, billed = revenue_blocks(conn, branch, f"{year}-01-01", f"{year}-12-31")
        months = [{"month": f"{year}-{m:02d}", "collected": collected.get(f"{year}-{m:02d}", 0),
                   "billed": (billed.get(f"{year}-{m:02d}") or (0, 0))[0],
                   "visits": (billed.get(f"{year}-{m:02d}") or (0, 0))[1]} for m in range(1, 13)]
        cats = [dict(r) for r in conn.execute(
            f"SELECT t.category AS name, SUM(t.cost) AS billed, COUNT(*) AS visits FROM treatments t "
            f"WHERE substr(t.visit_date,1,4)=? {bw} GROUP BY 1 ORDER BY 2 DESC", [str(year)] + bp)]
        branches = [dict(r) for r in conn.execute(
            "SELECT t.branch AS name, SUM(t.cost) AS billed, COUNT(*) AS visits FROM treatments t "
            "WHERE substr(t.visit_date,1,4)=? GROUP BY 1 ORDER BY 2 DESC", [str(year)])]
        undated = conn.execute(
            f"SELECT COALESCE(SUM(pay.amount),0) FROM payments pay JOIN treatments t ON t.id=pay.treatment_id "
            f"WHERE pay.paid_on IS NULL {bw}", bp).fetchone()[0]
        years = [int(r[0]) for r in conn.execute(
            "SELECT DISTINCT substr(visit_date,1,4) FROM treatments WHERE visit_date IS NOT NULL ORDER BY 1")]
    return {"year": year, "months": months, "categories": cats, "branches": branches,
            "undated_collected": undated, "years": years or [year]}


@app.get("/api/admin/audit")
def audit_list(scope: str = "changes", limit: int = 100, offset: int = 0, user=Depends(admin_only)):
    sensitive = ("patient_edit", "patient_delete", "treatment_edit", "treatment_delete", "payment_delete",
                 "password_change", "pin_change")
    where, params = "", []
    if scope == "changes":
        where = f"WHERE action IN ({','.join('?' * len(sensitive))})"
        params = list(sensitive)
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_logs {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM audit_logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                            params + [max(1, min(limit, 200)), max(0, offset)]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["changes"] = json.loads(d["changes"] or "[]")
        items.append(d)
    return {"items": items, "total": total}


@app.get("/api/admin/alerts")
def get_admin_alerts(limit: int = 50, user=Depends(admin_only)):
    sensitive = ("patient_edit", "patient_delete", "treatment_edit", "treatment_delete", "payment_delete")
    where = f"WHERE action IN ({','.join('?' * len(sensitive))})"
    params = list(sensitive)
    with db() as conn:
        unread = conn.execute(f"SELECT COUNT(*) FROM audit_logs {where} AND is_read=0", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM audit_logs {where} ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["changes"] = json.loads(d["changes"] or "[]")
        items.append(d)
    return {"items": items, "unread": unread}


class MarkAlertReadIn(BaseModel):
    alert_id: Optional[int] = None


@app.post("/api/admin/alerts/mark-read")
def mark_admin_alert_read(data: MarkAlertReadIn = None, user=Depends(admin_only)):
    with db() as conn:
        if data and data.alert_id:
            conn.execute("UPDATE audit_logs SET is_read=1 WHERE id=?", (data.alert_id,))
        else:
            conn.execute("UPDATE audit_logs SET is_read=1 WHERE is_read=0")
    return {"ok": True}


# ───────────────────────── staff accounts ─────────────────────────
class UserIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,30}$")
    full_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=6, max_length=200)
    role: str = "staff"


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=80)
    role: Optional[str] = None
    active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=200)


@app.get("/api/admin/users")
def list_users(user=Depends(admin_only)):
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id,username,full_name,role,active,created_at FROM users ORDER BY role, username")]


@app.post("/api/admin/users")
def create_user(data: UserIn, user=Depends(admin_only)):
    vchoice(data.role, ("admin", "staff"), "Vai trò")
    with db() as conn:
        try:
            cur = conn.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)",
                               (data.username.lower(), hash_secret(data.password), clean(data.full_name), data.role))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Tên đăng nhập đã tồn tại")
        audit(conn, user, "user_create", "user", cur.lastrowid, summary=f"Tạo tài khoản {data.username} ({data.role})")
    return {"id": cur.lastrowid}


@app.put("/api/admin/users/{uid}")
def update_user(uid: int, data: UserUpdate, user=Depends(admin_only)):
    with db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise HTTPException(404, "Không tìm thấy tài khoản")
        role = vchoice(data.role, ("admin", "staff"), "Vai trò") if data.role else target["role"]
        active = target["active"] if data.active is None else int(data.active)
        if uid == user["id"] and (not active or role != "admin"):
            raise HTTPException(422, "Không thể tự khóa hoặc hạ quyền chính mình")
        conn.execute("UPDATE users SET full_name=?, role=?, active=? WHERE id=?",
                     (clean(data.full_name) or target["full_name"], role, active, uid))
        if data.password:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_secret(data.password), uid))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        audit(conn, user, "user_edit", "user", uid, summary=f"Cập nhật tài khoản {target['username']}"
              + (" (đặt lại mật khẩu)" if data.password else ""))
    return {"ok": True}


@app.delete("/api/admin/users/{uid}")
def delete_user(uid: int, user=Depends(admin_only)):
    if uid == user["id"]:
        raise HTTPException(422, "Không thể tự xóa tài khoản của chính mình")
    with db() as conn:
        target = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise HTTPException(404, "Không tìm thấy tài khoản")
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        audit(conn, user, "user_delete", "user", uid, summary=f"Xóa tài khoản nhân viên {target['username']}")
    return {"ok": True}


class ClinicSettingsIn(BaseModel):
    clinic_name: str = Field(min_length=1, max_length=150)
    clinic_hotline: str = Field(default="", max_length=50)
    clinic_address: str = Field(default="", max_length=250)
    branches: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    dentists: list[str] = Field(default_factory=list)
    gemini_api_key: str = Field(default="", max_length=200)


@app.get("/api/admin/settings")
def get_app_settings(user=Depends(admin_only)):
    with db() as conn:
        def get_val(key, default):
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if r:
                try:
                    return json.loads(r["value"])
                except Exception:
                    return r["value"]
            return default

        return {
            "clinic_name": get_val("clinic_name", "Nha Khoa NiBi - BS. Thắng"),
            "clinic_hotline": get_val("clinic_hotline", "0976.838.901"),
            "clinic_address": get_val("clinic_address", "Ninh Vân"),
            "branches": get_val("branches", BRANCHES),
            "categories": get_val("categories", CATEGORIES),
            "dentists": get_val("dentists", ["Thắng"]),
            "gemini_api_key": get_val("gemini_api_key", ""),
        }


@app.post("/api/admin/settings")
def update_app_settings(data: ClinicSettingsIn, user=Depends(admin_only)):
    clean_branches = [clean(b) for b in data.branches if clean(b)] or BRANCHES
    clean_cats = [clean(c) for c in data.categories if clean(c)] or CATEGORIES
    clean_dentists = [clean(d) for d in data.dentists if clean(d)] or ["Thắng"]

    with db() as conn:
        def set_val(key, val):
            v_str = json.dumps(val, ensure_ascii=False) if isinstance(val, (list, dict)) else str(val)
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, v_str),
            )

        set_val("clinic_name", clean(data.clinic_name))
        set_val("clinic_hotline", clean(data.clinic_hotline))
        set_val("clinic_address", clean(data.clinic_address))
        set_val("branches", clean_branches)
        set_val("categories", clean_cats)
        set_val("dentists", clean_dentists)
        set_val("gemini_api_key", clean(data.gemini_api_key))

        audit(conn, user, "settings_update", summary="Cập nhật cấu hình phòng khám & ứng dụng")
    return {"ok": True}


# ───────────────────────── AI ASSISTANT ─────────────────────────
class AiIntakeIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class AiChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/ai/parse-intake")
def api_ai_parse_intake(data: AiIntakeIn, user=Depends(current_user)):
    """Bóc tách văn bản tự nhiên / giọng nói để tự động điền hồ sơ bệnh nhân."""
    parsed = parse_intake_with_ai(data.text)
    return {"ok": True, "data": parsed}


@app.post("/api/ai/chat")
def api_ai_chat(data: AiChatIn, user=Depends(current_user)):
    """Trợ lý AI hỏi đáp dữ liệu phòng khám thời gian thực."""
    is_admin = (user.get("role") == "admin")
    result = answer_clinic_query(data.message, is_admin=is_admin)
    return {"ok": True, **result}


@app.get("/api/admin/backup.db")
def download_backup_db(user=Depends(admin_only)):
    if not os.path.exists(dbm.DB_PATH):
        raise HTTPException(404, "Không tìm thấy file CSDL")
    filename = f"NiBi_Dental_Backup_{dt.date.today().isoformat()}.db"
    return FileResponse(dbm.DB_PATH, filename=filename, media_type="application/octet-stream")


@app.get("/api/admin/export-excel.xlsx")
@app.get("/api/export/quan-ly-nibi-2026.xlsx")
def download_nibi_excel_full(user=Depends(admin_only)):
    bio = generate_nibi_excel_workbook()
    today_str = dt.date.today().isoformat()
    filename = f"Quan_Ly_PK_NiBi_2026_{today_str}.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ───────────────────────── SUPPLIES & INVENTORY ─────────────────────────
class SupplyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = "Vật liệu tiêu hao"
    unit: str = "Chiếc"
    quantity: int = Field(default=0, ge=0)
    min_threshold: int = Field(default=5, ge=0)
    expiry_date: Optional[str] = None
    unit_price: int = Field(default=0, ge=0)
    supplier: str = ""
    branch: str = "Ninh Vân"
    notes: str = ""


class SupplyEditIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = "Vật liệu tiêu hao"
    unit: str = "Chiếc"
    quantity: int = Field(default=0, ge=0)
    min_threshold: int = Field(default=5, ge=0)
    expiry_date: Optional[str] = None
    unit_price: int = Field(default=0, ge=0)
    supplier: str = ""
    branch: str = "Ninh Vân"
    notes: str = ""


class StockAdjustIn(BaseModel):
    delta: int = Field(description="Dương: Nhập, Âm: Xuất")
    reason: str = ""


class SupplyAiParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def compute_date_status(exp_str: Optional[str], today: dt.date):
    if not exp_str:
        return None, "none"
    try:
        exp = dt.date.fromisoformat(str(exp_str)[:10])
        days_left = (exp - today).days
        if days_left <= 30:
            return days_left, "red"
        elif days_left <= 180:
            return days_left, "yellow"
        elif days_left > 365:
            return days_left, "green"
        else:
            return days_left, "normal"
    except Exception:
        return None, "none"


def has_vietnamese_accent(text: str) -> bool:
    norm = clean(text)
    stripped = strip_accents(norm)
    return norm.lower() != stripped.lower()


def supply_matches(query: str, item: dict) -> bool:
    if not query or not query.strip():
        return True
    q = query.strip()
    name_code_raw = f"{item.get('name') or ''} {item.get('code') or ''}"
    supplier_raw = item.get("supplier") or ""

    name_code_norm = strip_accents(name_code_raw).lower()
    supplier_norm = strip_accents(supplier_raw).lower()

    if has_vietnamese_accent(q):
        q_tokens = q.lower().split()
        for tok in q_tokens:
            pattern_accent = r'(?<!\w)' + re.escape(tok) + r'(?!\w)'
            in_name = bool(re.search(pattern_accent, name_code_raw.lower()))
            if not in_name:
                if len(tok) >= 4 and tok in supplier_raw.lower():
                    continue
                return False
        return True
    else:
        q_tokens = strip_accents(q).lower().split()
        for tok in q_tokens:
            if len(tok) <= 2:
                pattern = r'(?<![a-z0-9])' + re.escape(tok) + r'(?![a-z0-9])'
                if not re.search(pattern, name_code_norm):
                    return False
            else:
                full_norm = f"{name_code_norm} {supplier_norm}"
                if tok not in full_norm:
                    return False
        return True


@app.get("/api/supplies")
def get_supplies(
    q: Optional[str] = None,
    category: Optional[str] = None,
    filter_type: Optional[str] = "all",
    branch: Optional[str] = None,
    user=Depends(current_user),
):
    today = dt.date.today()
    with db() as conn:
        sql = "SELECT * FROM supplies WHERE 1=1"
        params = []
        if branch:
            sql += " AND branch = ?"
            params.append(branch)
        if category and category != "Tất cả":
            sql += " AND category = ?"
            params.append(category)

        sql += " ORDER BY CASE WHEN quantity <= min_threshold THEN 0 ELSE 1 END, id DESC"
        rows = conn.execute(sql, params).fetchall()

        items = []
        low_stock_count = 0
        exp_red_count = 0
        exp_yellow_count = 0
        exp_green_count = 0
        total_value = 0

        for r in rows:
            d = dict(r)
            days_left, date_status = compute_date_status(d.get("expiry_date"), today)
            d["days_left"] = days_left
            d["date_status"] = date_status
            is_low = int(d.get("quantity") or 0) <= int(d.get("min_threshold") or 0)
            d["is_low_stock"] = is_low

            if is_low:
                low_stock_count += 1
            if date_status == "red":
                exp_red_count += 1
            elif date_status == "yellow":
                exp_yellow_count += 1
            elif date_status == "green":
                exp_green_count += 1

            total_value += int(d.get("quantity") or 0) * int(d.get("unit_price") or 0)

            if q and q.strip() and not supply_matches(q, d):
                continue

            if filter_type == "low_stock" and not is_low:
                continue
            if filter_type == "red" and date_status != "red":
                continue
            if filter_type == "yellow" and date_status != "yellow":
                continue
            if filter_type == "green" and date_status != "green":
                continue

            items.append(d)

    return {
        "items": items,
        "summary": {
            "total_items": len(rows),
            "low_stock_count": low_stock_count,
            "exp_red_count": exp_red_count,
            "exp_yellow_count": exp_yellow_count,
            "exp_green_count": exp_green_count,
            "total_inventory_value": total_value if user["role"] == "admin" else 0,
        },
    }


@app.post("/api/supplies")
def create_supply(req: SupplyIn, user=Depends(current_user)):
    name = req.name.strip()
    if not name:
        raise HTTPException(422, "Tên vật tư không được để trống")
    category = req.category if req.category in SUPPLY_CATEGORIES else "Vật liệu tiêu hao"
    unit = req.unit if req.unit in SUPPLY_UNITS else "Chiếc"
    branch = req.branch if req.branch in BRANCHES else BRANCHES[0]
    exp = vdate(req.expiry_date, "Hạn sử dụng", required=False)

    with db() as conn:
        r_max = conn.execute("SELECT MAX(id) FROM supplies").fetchone()
        next_id = (r_max[0] or 0) + 1
        code = f"VT{next_id:05d}"

        cur = conn.execute(
            """INSERT INTO supplies(code, name, category, unit, quantity, min_threshold, expiry_date, unit_price, supplier, branch, notes)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, name, category, unit, req.quantity, req.min_threshold, exp, req.unit_price, req.supplier.strip(), branch, req.notes.strip()),
        )
        sup_id = cur.lastrowid

        conn.execute(
            """INSERT INTO supply_transactions(supply_id, supply_name, action, delta, quantity_before, quantity_after, reason, created_by)
               VALUES(?, ?, 'init', ?, 0, ?, 'Tạo mới vật tư', ?)""",
            (sup_id, name, req.quantity, req.quantity, user.get("full_name") or user.get("username")),
        )

        audit(
            conn,
            user,
            "create",
            entity="supplies",
            entity_id=sup_id,
            summary=f"Tạo vật tư mới [{code}] {name} (SL: {req.quantity} {unit})",
        )

        row = conn.execute("SELECT * FROM supplies WHERE id = ?", (sup_id,)).fetchone()
        res = dict(row)
        days_left, date_status = compute_date_status(res.get("expiry_date"), dt.date.today())
        res["days_left"] = days_left
        res["date_status"] = date_status
        res["is_low_stock"] = res["quantity"] <= res["min_threshold"]
        return res


@app.put("/api/supplies/{sid}")
def update_supply(sid: int, req: SupplyEditIn, user=Depends(current_user)):
    name = req.name.strip()
    if not name:
        raise HTTPException(422, "Tên vật tư không được để trống")
    category = req.category if req.category in SUPPLY_CATEGORIES else "Vật liệu tiêu hao"
    unit = req.unit if req.unit in SUPPLY_UNITS else "Chiếc"
    branch = req.branch if req.branch in BRANCHES else BRANCHES[0]
    exp = vdate(req.expiry_date, "Hạn sử dụng", required=False)

    with db() as conn:
        old = conn.execute("SELECT * FROM supplies WHERE id = ?", (sid,)).fetchone()
        if not old:
            raise HTTPException(404, "Không tìm thấy vật tư")

        old_dict = dict(old)
        old_qty = old_dict["quantity"]
        new_qty = req.quantity

        conn.execute(
            """UPDATE supplies SET name=?, category=?, unit=?, quantity=?, min_threshold=?, expiry_date=?, unit_price=?, supplier=?, branch=?, notes=?, updated_at=datetime('now','localtime')
               WHERE id=?""",
            (name, category, unit, new_qty, req.min_threshold, exp, req.unit_price, req.supplier.strip(), branch, req.notes.strip(), sid),
        )

        if old_qty != new_qty:
            delta = new_qty - old_qty
            conn.execute(
                """INSERT INTO supply_transactions(supply_id, supply_name, action, delta, quantity_before, quantity_after, reason, created_by)
                   VALUES(?, ?, 'adjust', ?, ?, ?, 'Cân chỉnh tồn kho', ?)""",
                (sid, name, delta, old_qty, new_qty, user.get("full_name") or user.get("username")),
            )

        audit(
            conn,
            user,
            "update",
            entity="supplies",
            entity_id=sid,
            summary=f"Cập nhật thông tin vật tư [{old_dict.get('code')}] {name}",
        )

        row = conn.execute("SELECT * FROM supplies WHERE id = ?", (sid,)).fetchone()
        res = dict(row)
        days_left, date_status = compute_date_status(res.get("expiry_date"), dt.date.today())
        res["days_left"] = days_left
        res["date_status"] = date_status
        res["is_low_stock"] = res["quantity"] <= res["min_threshold"]
        return res


@app.post("/api/supplies/{sid}/adjust")
def adjust_stock(sid: int, req: StockAdjustIn, user=Depends(current_user)):
    """1-touch quick in/out for assistants/receptionists without requiring admin password."""
    if req.delta == 0:
        raise HTTPException(422, "Số lượng điều chỉnh phải khác 0")

    with db() as conn:
        row = conn.execute("SELECT * FROM supplies WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "Không tìm thấy vật tư")

        sup = dict(row)
        curr_qty = sup["quantity"]
        new_qty = curr_qty + req.delta

        if new_qty < 0:
            raise HTTPException(
                400,
                f"Kho hiện chỉ còn {curr_qty} {sup['unit']}, không thể xuất {abs(req.delta)} {sup['unit']}"
            )

        conn.execute(
            "UPDATE supplies SET quantity=?, updated_at=datetime('now','localtime') WHERE id=?",
            (new_qty, sid),
        )

        action = "import" if req.delta > 0 else "export"
        default_reason = f"Nhập thêm {req.delta} {sup['unit']}" if req.delta > 0 else f"Xuất dùng {abs(req.delta)} {sup['unit']}"
        reason = req.reason.strip() if req.reason and req.reason.strip() else default_reason

        conn.execute(
            """INSERT INTO supply_transactions(supply_id, supply_name, action, delta, quantity_before, quantity_after, reason, created_by)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, sup["name"], action, req.delta, curr_qty, new_qty, reason, user.get("full_name") or user.get("username")),
        )

        audit(
            conn,
            user,
            action,
            entity="supplies",
            entity_id=sid,
            summary=f"{'Nhập' if req.delta > 0 else 'Xuất'} [{sup.get('code')}] {sup['name']}: {req.delta:+d} {sup['unit']} (Kho: {curr_qty} -> {new_qty}) - Lý do: {reason}",
        )

        row_updated = conn.execute("SELECT * FROM supplies WHERE id = ?", (sid,)).fetchone()
        res = dict(row_updated)
        days_left, date_status = compute_date_status(res.get("expiry_date"), dt.date.today())
        res["days_left"] = days_left
        res["date_status"] = date_status
        res["is_low_stock"] = res["quantity"] <= res["min_threshold"]
        return {"ok": True, "supply": res}


@app.delete("/api/supplies/{sid}")
def delete_supply(sid: int, user=Depends(admin_only)):
    with db() as conn:
        row = conn.execute("SELECT * FROM supplies WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "Không tìm thấy vật tư")
        sup = dict(row)
        conn.execute("DELETE FROM supplies WHERE id = ?", (sid,))
        audit(
            conn,
            user,
            "delete",
            entity="supplies",
            entity_id=sid,
            summary=f"Xóa vật tư [{sup.get('code')}] {sup['name']}",
        )
    return {"ok": True}


@app.get("/api/supplies/transactions")
def get_supply_transactions(supply_id: Optional[int] = None, limit: int = 50, user=Depends(current_user)):
    with db() as conn:
        if supply_id:
            rows = conn.execute(
                "SELECT * FROM supply_transactions WHERE supply_id = ? ORDER BY id DESC LIMIT ?",
                (supply_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM supply_transactions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/supplies/ai-parse")
def ai_parse_supply(req: SupplyAiParseIn, user=Depends(current_user)):
    return {"ok": True, "data": parse_supply_with_ai(req.text)}


# ───────────────────────── static ─────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

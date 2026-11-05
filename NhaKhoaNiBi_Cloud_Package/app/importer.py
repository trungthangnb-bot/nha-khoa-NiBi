"""Import 'Quản_Lý_PK_NiBi_2026.xlsx' into the NiBi database.

Sheets used
  * 'Dữ liệu nhập' - every visit at Ninh Vân (amounts are in NGHÌN đồng).
  * 'NCCĐ'         - orthodontic (niềng) payment plans at Phố Dâu / Cửu Yên (amounts in TRIỆU đồng).
Sheets 'Implant.' has no patient names, the rest are empty templates, so they are skipped.

Usage:  python -m app.importer path/to/file.xlsx --reset
"""
import argparse
import datetime as dt
import os
import sys
import warnings

import openpyxl

from . import db as dbm
from .textutil import clean, split_phone, strip_accents, vi_title

warnings.filterwarnings("ignore")

CAT_MAP = {strip_accents(c): c for c in dbm.CATEGORIES}
BRANCH_MAP = {
    "ninh van": "Ninh Vân", "dau": "Phố Dâu", "pho dau": "Phố Dâu", "cuu yen": "Cửu Yên",
}
METHOD_MAP = {"tien mat": "Tiền mặt", "c.khoan": "Chuyển khoản", "chuyen khoan": "Chuyển khoản", "ck": "Chuyển khoản"}


def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return None


def year_from(v, this_year):
    """Spreadsheet mixes birth years (1996) and ages (20) in one column."""
    n = num(v)
    if n is None:
        return None
    n = int(n)
    if 1900 <= n <= this_year:
        return n
    if 0 <= n <= 110:
        return this_year - n
    return None


def as_date(v):
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return None


class Importer:
    def __init__(self, conn, this_year):
        self.conn, self.year = conn, this_year
        self.patients = {}  # (normalized name, yob) -> id
        self.report = {"patients": 0, "treatments": 0, "payments": 0, "appointments": 0, "warnings": []}

    def warn(self, msg):
        self.report["warnings"].append(msg)

    def patient(self, name, yob, phone_raw, address, branch):
        key = (strip_accents(name), yob)
        phone, note = split_phone(phone_raw)
        pid = self.patients.get(key)
        if pid:  # fill gaps on repeat visits instead of duplicating the patient
            row = self.conn.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
            upd = {}
            if phone and not row["phone"]:
                upd["phone"] = phone
            if note and not row["phone_note"]:
                upd["phone_note"] = note
            if address and not row["address"]:
                upd["address"] = address
            if upd:
                merged = {**dict(row), **upd}
                upd["search_text"] = dbm.patient_search_text(
                    merged["name"], merged["phone"], merged["phone_note"], merged["address"], merged["code"])
                sets = ", ".join(f"{k}=?" for k in upd)
                self.conn.execute(f"UPDATE patients SET {sets} WHERE id=?", (*upd.values(), pid))
            return pid
        cur = self.conn.execute(
            "INSERT INTO patients(name,phone,phone_note,yob,address,branch) VALUES(?,?,?,?,?,?)",
            (name, phone, note, yob, address, branch))
        pid = cur.lastrowid
        code = f"BN{pid:05d}"
        self.conn.execute(
            "UPDATE patients SET code=?, search_text=? WHERE id=?",
            (code, dbm.patient_search_text(name, phone, note, address, code), pid))
        self.patients[key] = pid
        self.report["patients"] += 1
        return pid

    def treatment(self, pid, visit_date, branch, category, dentist, procedure, cost, source):
        cur = self.conn.execute(
            "INSERT INTO treatments(patient_id,visit_date,branch,category,dentist,procedure,cost,source,created_by)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, visit_date, branch, category, dentist, procedure, cost, source, "import"))
        self.report["treatments"] += 1
        return cur.lastrowid

    def payment(self, tid, pid, amount, method, paid_on, note):
        if amount <= 0:
            return
        self.conn.execute(
            "INSERT INTO payments(treatment_id,patient_id,amount,method,paid_on,note,created_by) VALUES(?,?,?,?,?,?,?)",
            (tid, pid, amount, method, paid_on, note, "import"))
        self.report["payments"] += 1

    # ---- sheet: Dữ liệu nhập ------------------------------------------------
    def visits(self, ws):
        rows = ws.iter_rows(min_row=2, values_only=True)
        for n, r in enumerate(rows, start=2):
            r = list(r) + [None] * 17
            name = vi_title(r[2])
            has_money = bool(num(r[10]) or num(r[12]))
            if not name:
                if not (as_date(r[1]) and has_money):
                    continue  # dòng trống thật sự
                name = "Khách chưa ghi tên"
                self.warn(f"Dữ liệu nhập dòng {n}: có tiền ({clean(r[10])}k) nhưng không ghi tên -> gom vào hồ sơ 'Khách chưa ghi tên'")
            branch = BRANCH_MAP.get(strip_accents(r[0]), "Ninh Vân")
            visit = as_date(r[1])
            if not visit:
                self.warn(f"Dữ liệu nhập dòng {n} ({name}): ngày khám không hợp lệ -> bỏ dòng")
                continue
            yob = year_from(r[3], self.year)
            pid = self.patient(name, yob, r[5], vi_title(r[6]), branch)
            cat = CAT_MAP.get(strip_accents(r[7]), "Tổng quát")
            procedure = clean(r[9]) or "Khám & điều trị"
            cost = round((num(r[10]) or 0) * 1000)
            paid = round((num(r[12]) or 0) * 1000)
            if paid > cost:
                self.warn(f"Dữ liệu nhập dòng {n} ({name}): đã thanh toán {paid} > tổng {cost} -> nâng tổng bằng số đã thu")
                cost = paid
            expected_debt = round((num(r[13]) or 0) * 1000)
            if expected_debt != cost - paid:
                self.warn(f"Dữ liệu nhập dòng {n} ({name}): cột CÒN NỢ={expected_debt} khác tổng-đã thu={cost - paid} (dùng tổng-đã thu)")
            tid = self.treatment(pid, visit, branch, cat, vi_title(r[8]) or "Thắng", procedure, cost, "Dữ liệu nhập")
            method = METHOD_MAP.get(strip_accents(r[11]), "Tiền mặt")
            self.payment(tid, pid, paid, method, visit, "Nhập từ Excel")
            nxt = as_date(r[15])
            if nxt:
                self.conn.execute(
                    "INSERT INTO appointments(patient_id,kind,appt_date,branch,reason,status) VALUES(?,?,?,?,?,?)",
                    (pid, "tai_kham", nxt, branch, f"Hẹn điều trị tiếp: {procedure}", "Chờ đến"))
                self.report["appointments"] += 1

    # ---- sheet: NCCĐ ---------------------------------------------------------
    def ortho(self, ws):
        for n, r in enumerate(ws.iter_rows(values_only=True), start=1):
            r = list(r) + [None] * 10
            name = vi_title(r[1])
            if not name or strip_accents(name) in ("ho ten", "tuoi"):
                continue
            branch = BRANCH_MAP.get(strip_accents(r[8]))
            if not branch:
                self.warn(f"NCCĐ dòng {n} ({name}): thiếu phòng khám -> gán 'Cửu Yên'")
                branch = "Cửu Yên"
            total, paid, left = num(r[4]), num(r[5]) or 0, num(r[6])
            if total is None:
                # Cột TỔNG để trống: không đoán số nợ, coi tổng = số đã thu (nợ 0) để chủ phòng khám rà lại.
                total = paid
                self.warn(f"NCCĐ dòng {n} ({name}): chưa ghi TỔNG (đã thu {paid:g} triệu) -> coi tổng = đã thu, nợ 0. Cần bổ sung tổng nếu còn nợ")
            else:
                if paid > total:
                    self.warn(f"NCCĐ dòng {n} ({name}): đã thu {paid:g} > tổng {total:g} -> nâng tổng bằng số đã thu")
                    total = paid
                if left is not None and abs((total - paid) - left) > 1e-6:
                    self.warn(f"NCCĐ dòng {n} ({name}): CÒN LẠI={left:g} khác tổng-đã thu={total - paid:g} (dùng tổng-đã thu)")
            pid = self.patient(name, year_from(r[2], self.year), r[7], vi_title(r[3]), branch)
            tid = self.treatment(pid, None, branch, "Chỉnh nha", "Thắng",
                                 "Niềng răng cố định (NCCĐ)", round(total * 1_000_000), "NCCĐ")
            self.payment(tid, pid, round(paid * 1_000_000), "", None, "Nhập từ sheet NCCĐ (chưa có ngày thu)")


def import_workbook(path, reset=False):
    dbm.init_db()
    conn = dbm.connect()
    try:
        have = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        if have and not reset:
            raise SystemExit(f"CSDL đã có {have} bệnh nhân. Chạy lại với --reset để xóa và nhập lại.")
        if reset:
            for t in ("appointments", "payments", "treatments", "patients"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('appointments','payments','treatments','patients')")
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        imp = Importer(conn, dt.date.today().year)
        imp.visits(wb["Dữ liệu nhập"])
        imp.ortho(wb["NCCĐ"])
        conn.commit()
        return imp.report
    finally:
        conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    rep = import_workbook(a.xlsx, a.reset)
    print({k: v for k, v in rep.items() if k != "warnings"})
    report_path = os.path.join(os.path.dirname(dbm.DB_PATH), "import_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Báo cáo nhập Excel - {len(rep['warnings'])} dòng cần rà soát\n")
        f.writelines(f"- {w}\n" for w in rep["warnings"])
    print(f"{len(rep['warnings'])} cảnh báo -> {report_path}")

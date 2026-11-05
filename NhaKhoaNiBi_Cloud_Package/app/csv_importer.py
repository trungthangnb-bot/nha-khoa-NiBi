"""Import the user's new CSV data into Nha Khoa NiBi."""
import csv
import datetime as dt
import io
import os
import re
import sqlite3
import unicodedata

from app import db as dbm
from app.textutil import clean, strip_accents, vi_title

def parse_money(v):
    if v is None or v == "":
        return 0
    # Clean up string
    s = str(v).replace(",", ".").replace(" ", "").strip()
    try:
        val = float(s)
        # Amounts in this CSV are in THOUSANDS (k) VND (e.g. 200 -> 200,000; 45000 -> 45,000,000)
        return int(round(val * 1000))
    except ValueError:
        return 0

def clean_phone(p):
    if not p:
        return ""
    p = str(p).strip()
    # sometimes phone is '-'
    if p in ("-", "--", "none", "nan"):
        return ""
    return p

def map_branch(b):
    b = strip_accents(b or "")
    if "van" in b:
        return "Ninh Vân"
    if "dau" in b:
        return "Phố Dâu"
    if "cuu" in b or "yen" in b:
        return "Cửu Yên"
    return "Ninh Vân"

def map_category(cat):
    c = strip_accents(cat or "").lower()
    if "nho" in c:
        return "Nhổ răng"
    if "tuy" in c:
        return "Điều trị tủy"
    if "gia" in c or "phuc hinh" in c or "su" in c:
        return "Răng giả"
    if "chinh nha" in c or "nieng" in c:
        return "Chỉnh nha"
    if "implant" in c:
        return "Implant"
    return "Tổng quát"

def map_method(m):
    s = strip_accents(m or "").lower()
    if "khoan" in s or "ck" in s:
        return "Chuyển khoản"
    return "Tiền mặt"

def parse_date(d):
    if not d:
        return None
    d = str(d).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", d)
    if m:
        y, mo, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{day:02d}"
    return None

def import_csv_data(csv_path, reset=True):
    with dbm.db() as conn:
        if reset:
            print("Resetting patients, treatments, payments, appointments...")
            for table in ("appointments", "payments", "treatments", "patients"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('appointments','payments','treatments','patients')")
            conn.commit()

        # Parse CSV
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader, None)

            patients_by_key = {} # (name_norm, phone) -> pid
            imported_rows = 0
            patient_count = 0
            treatment_count = 0
            payment_count = 0
            appt_count = 0

            for row_idx, row in enumerate(reader, start=2):
                if not row or len(row) < 3:
                    continue
                # PK, ngày, Họ Tên, Năm Sinh, Tuổi, SĐT, địa chỉ, Khách hàng, Nha sỹ, Điều Trị, TỔNG TIỀN, Thanh Toán, Đã Thanh Toán, CÒN NỢ, Trạng Thái, Ngày Hẹn Đtrị
                # Check if this is template / metadata row
                row_str = "".join(row)
                if "BẢNG THIẾT LẬP" in row_str or "GIỚI THIỆU VÀ HƯỚNG DẪN" in row_str or "Copyright" in row_str or "PHIẾU BÁN HÀNG" in row_str or "BẢNG DỮ LIỆU" in row_str:
                    break

                name_raw = row[2].strip() if len(row) > 2 else ""
                # Also handle row where name might be in row[1] if comma missing
                if not name_raw:
                    # check if row is empty
                    if not any(x.strip() for x in row):
                        continue
                    # Check special case row like `,ngô minh vỹ,1955,implant r47...`
                    if len(row) > 1 and row[1].strip() and not row[1].strip().startswith("202"):
                        name_raw = row[1].strip()
                    else:
                        continue

                name_clean = vi_title(name_raw)
                if not name_clean or name_clean.lower() in ("khách", "tây", "cr", "bn", "all"):
                    # Use identifiable fallback
                    if name_clean.lower() == "tây":
                        name_clean = "Khách Nước Ngoài (Tây)"
                    elif name_clean.lower() in ("cr", "bn", "khách"):
                        name_clean = f"Khách vãng lai (Dòng {row_idx})"
                    else:
                        name_clean = f"Khách hàng {name_clean}"

                branch = map_branch(row[0] if len(row) > 0 else "")
                visit_date = parse_date(row[1]) if len(row) > 1 else None
                yob = None
                if len(row) > 3 and row[3].strip().isdigit():
                    y = int(row[3].strip())
                    if 1920 <= y <= dt.date.today().year:
                        yob = y
                phone = clean_phone(row[5]) if len(row) > 5 else ""
                address = row[6].strip() if len(row) > 6 else ""
                category = map_category(row[7] if len(row) > 7 else "")
                dentist = row[8].strip() if len(row) > 8 and row[8].strip() else "Thắng"
                procedure = row[9].strip() if len(row) > 9 and row[9].strip() else f"Khám {category}"
                total_cost = parse_money(row[10]) if len(row) > 10 else 0
                pay_method = map_method(row[11]) if len(row) > 11 else "Tiền mặt"
                paid_amount = parse_money(row[12]) if len(row) > 12 else 0
                debt_amount = parse_money(row[13]) if len(row) > 13 else 0
                status = row[14].strip() if len(row) > 14 else ""
                appt_date = parse_date(row[15]) if len(row) > 15 else None

                # Find or create patient
                key = (strip_accents(name_clean).lower(), phone)
                pid = patients_by_key.get(key)
                if not pid:
                    cur = conn.execute(
                        "INSERT INTO patients(name, phone, yob, address, branch, medical_history, notes) VALUES(?,?,?,?,?,?,?)",
                        (name_clean, phone, yob, address, branch, "", "")
                    )
                    pid = cur.lastrowid
                    code = f"BN{pid:05d}"
                    search_text = dbm.patient_search_text(name_clean, phone, "", address, code)
                    conn.execute("UPDATE patients SET code=?, search_text=? WHERE id=?", (code, search_text, pid))
                    patients_by_key[key] = pid
                    patient_count += 1

                # Insert treatment
                cur = conn.execute(
                    "INSERT INTO treatments(patient_id, visit_date, branch, category, dentist, procedure, tooth, cost, notes, created_by) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (pid, visit_date or dt.date.today().isoformat(), branch, category, dentist, procedure, "", total_cost, f"Trạng thái gốc: {status}" if status else "", "import")
                )
                tid = cur.lastrowid
                treatment_count += 1

                # Insert payment if paid > 0
                if paid_amount > 0:
                    conn.execute(
                        "INSERT INTO payments(treatment_id, patient_id, amount, method, paid_on, note, created_by) VALUES(?,?,?,?,?,?,?)",
                        (tid, pid, paid_amount, pay_method, visit_date or dt.date.today().isoformat(), "Thanh toán đợt khám", "import")
                    )
                    payment_count += 1

                # Insert appointment if appt_date
                if appt_date:
                    conn.execute(
                        "INSERT INTO appointments(patient_id, kind, appt_date, branch, reason, status) VALUES(?,?,?,?,?,?)",
                        (pid, "tai_kham", appt_date, branch, f"Hẹn khám sau: {procedure}", "Chờ đến")
                    )
                    appt_count += 1

                imported_rows += 1

            conn.commit()

            print(f"✅ Hoàn tất import dữ liệu mới:")
            print(f"- Tổng số dòng điều trị xử lý: {imported_rows}")
            print(f"- Số bệnh nhân tạo mới: {patient_count}")
            print(f"- Số ca điều trị: {treatment_count}")
            print(f"- Số khoản thanh toán: {payment_count}")
            print(f"- Số lịch hẹn tạo mới: {appt_count}")

            # Calculate and display stats
            stats = conn.execute('''
                WITH tp AS (
                    SELECT t.*, COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id=t.id),0) AS paid FROM treatments t
                )
                SELECT COUNT(DISTINCT patient_id) AS pts,
                       SUM(cost) AS total_billed,
                       SUM(paid) AS total_paid,
                       SUM(MAX(cost-paid, 0)) AS total_debt
                FROM tp
            ''').fetchone()
            print(f"📊 Thống kê tài chính sau khi import:")
            print(f"  + Tổng chi phí phát sinh: {stats['total_billed']:,} đ")
            print(f"  + Đã thực thu vào quỹ:   {stats['total_paid']:,} đ")
            print(f"  + Tổng công nợ còn lại:  {stats['total_debt']:,} đ")

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "data/new_data.csv"
    import_csv_data(csv_file, reset=True)

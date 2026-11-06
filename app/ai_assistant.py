"""
Nha Khoa NiBi - BS. Thắng
Mô-đun Trợ Lý Trí Tuệ Nhân Tạo (AI Assistant)
- Bóc tách văn bản tự nhiên / giọng nói để tự động điền form tiếp đón bệnh nhân
- Hỏi đáp thông minh về doanh thu, công nợ, lịch hẹn với Google Gemini & Live SQLite
"""
import os
import re
import json
import time
import datetime as dt
import urllib.request
import urllib.error

from .db import db
from .textutil import strip_accents, clean

KNOWN_ADDRESSES = [
    "Xí Nghiệp", "Xuân Vũ", "Hệ", "Phú Lăng", "Thượng", "Dưỡng Thượng",
    "Vũ Xá", "Dưỡng Hạ", "Dưỡng Trung", "Tam Điệp", "Ninh Vân", "Phố Dâu", "Cửu Yên",
    "Mai Sơn", "Ninh Bình", "Yên Khánh", "Hoa Lư", "Gia Viễn"
]

def get_gemini_api_key() -> str:
    # 1. Environment variable
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    # 2. Database settings
    try:
        with db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'gemini_api_key'").fetchone()
            if row and row[0]:
                return row[0].strip()
    except Exception:
        pass
    return ""

def call_gemini_api(prompt: str, system_instruction: str = "", api_key: str = "", json_mode: bool = False) -> str:
    if not api_key:
        api_key = get_gemini_api_key()
    if not api_key:
        return ""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1024
        }
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            candidates = res_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
    except Exception as e:
        print(f"Gemini API Error: {e}")
    return ""

def parse_intake_fallback(text: str) -> dict:
    """Smart Vietnamese rule-based intake extractor when Gemini API key is not present."""
    raw = text.strip()
    today = dt.date.today()
    current_year = today.year

    res = {
        "name": "",
        "phone": "",
        "yob": None,
        "age": None,
        "address": "",
        "branch": "Ninh Vân",
        "category": "Tổng quát",
        "procedure": "",
        "tooth": "",
        "cost": 0,
        "paid": 0,
        "method": "Tiền mặt",
        "next_date": "",
        "notes": ""
    }

    # 1. Phone number
    phone_m = re.search(r'(0[3|5|7|8|9]\d{8})', raw)
    if phone_m:
        res["phone"] = phone_m.group(1)

    # 2. Birth year or Age
    yob_m = re.search(r'(?:sinh\s*năm|sn|năm\s*sinh)?\s*(19\d{2}|20\d{2})', raw, re.IGNORECASE)
    age_m = re.search(r'(\d{1,2})\s*(?:tuổi|t\b)', raw, re.IGNORECASE)
    if yob_m:
        res["yob"] = int(yob_m.group(1))
        res["age"] = current_year - res["yob"]
    elif age_m:
        res["age"] = int(age_m.group(1))
        res["yob"] = current_year - res["age"]

    # 3. Branch
    if re.search(r'phố\s*dâu', raw, re.IGNORECASE):
        res["branch"] = "Phố Dâu"
    elif re.search(r'cửu\s*yên', raw, re.IGNORECASE):
        res["branch"] = "Cửu Yên"

    # 4. Address matching from known dental clinic village list
    for addr in KNOWN_ADDRESSES:
        if re.search(r'\b' + re.escape(addr) + r'\b', raw, re.IGNORECASE):
            res["address"] = addr
            break

    # 5. Category & Procedure
    lower = raw.lower()
    if any(k in lower for k in ["nhổ", "nho rang"]):
        res["category"] = "Nhổ răng"
    elif any(k in lower for k in ["răng giả", "răng sứ", "katana", "zirconia", "cercon", "tháo lắp", "cầu răng"]):
        res["category"] = "Răng giả"
    elif any(k in lower for k in ["chỉnh nha", "niềng", "mắc cài"]):
        res["category"] = "Chỉnh nha"
    elif any(k in lower for k in ["tủy", "diệt tủy"]):
        res["category"] = "Điều trị tủy"
    elif any(k in lower for k in ["implant", "cắm ghép"]):
        res["category"] = "Implant"
    else:
        res["category"] = "Tổng quát"

    # Tooth position
    tooth_m = re.search(r'(?:răng|r)\s*([1-4][1-8]|[5-8][1-5])', raw, re.IGNORECASE)
    if tooth_m:
        res["tooth"] = "R" + tooth_m.group(1)

    # 6. Money: cost and paid
    def parse_money_val(val_str: str, unit: str) -> int:
        v = float(val_str.replace(',', '.'))
        u = unit.lower() if unit else ''
        if 'tr' in u or 'triệu' in u:
            return int(v * 1000000)
        elif 'k' in u or 'nghìn' in u or 'ngàn' in u:
            return int(v * 1000)
        else:
            return int(v * 1000) if v < 10000 else int(v)

    # Search for paid
    paid_m = re.search(r'(?:đã trả|đã thanh toán|trả|đưa|đóng)\s*(\d+[\.,]?\d*)\s*(k|nghìn|ngàn|triệu|tr\b)?', raw, re.IGNORECASE)
    if paid_m:
        res["paid"] = parse_money_val(paid_m.group(1), paid_m.group(2) or "k")

    # Search for cost / total
    cost_m = re.search(r'(?:giá|chi phí|tổng|hết|mức|tiền)?\s*(\d+[\.,]?\d*)\s*(k|nghìn|ngàn|triệu|tr\b)', raw, re.IGNORECASE)
    if cost_m:
        res["cost"] = parse_money_val(cost_m.group(1), cost_m.group(2))
    elif res["paid"] > 0:
        res["cost"] = res["paid"]

    if res["paid"] > res["cost"]:
        res["cost"] = res["paid"]

    # Method
    if any(k in lower for k in ["chuyển khoản", "ck", "bắn tiền", "quẹt thẻ"]):
        res["method"] = "Chuyển khoản"
    else:
        res["method"] = "Tiền mặt"

    # 7. Next appointment date
    if "tuần sau" in lower:
        res["next_date"] = (today + dt.timedelta(days=7)).isoformat()
    elif "tháng sau" in lower:
        res["next_date"] = (today + dt.timedelta(days=30)).isoformat()
    else:
        days_m = re.search(r'(?:hẹn)?\s*(\d+)\s*ngày\s*sau', lower)
        if days_m:
            res["next_date"] = (today + dt.timedelta(days=int(days_m.group(1)))).isoformat()

    # 8. Procedure title extraction
    # Clean up obvious words to form clean procedure and name
    proc_keywords = ["nhổ", "hàn", "trám", "lấy cao răng", "crvl", "bọc sứ", "cầu răng", "niềng", "tẩy trắng", "điều trị tủy"]
    for pk in proc_keywords:
        if pk in lower:
            # find match start
            idx = lower.find(pk)
            # take until punctuation or stop word
            sub = raw[idx:]
            end_match = re.search(r'(giá|hết|tiền|đã trả|trả|hẹn|$|,|\.)', sub, re.IGNORECASE)
            if end_match and end_match.start() > 0:
                res["procedure"] = sub[:end_match.start()].strip()
            else:
                res["procedure"] = sub.strip()
            break

    if not res["procedure"]:
        res["procedure"] = res["category"]

    # 9. Name extraction
    # Remove phrases like 'Bệnh nhân', 'Khách', 'Anh', 'Chị', 'Cô', 'Chú', 'Bác'
    cleaned_name = raw
    name_m = re.search(r'(?:bệnh nhân|bệnh nhân mới|bn|khách|anh|chị|cô|chú|bác|em)\s+([A-ZÀ-Ỹa-zà-ỹ\s]+?)(?=\s+(?:sinh|sn|năm|\d{4}|ở|tại|sđt|số|đt|nhổ|hàn|làm|bọc|khám|giá))', raw, re.IGNORECASE)
    if name_m:
        res["name"] = name_m.group(1).strip().title()
    else:
        # Fallback first 2-4 capitalized or clean words
        words = raw.split()[:4]
        res["name"] = " ".join(words).title()

    return res

def parse_intake_with_ai(text: str, api_key: str = "") -> dict:
    """Uses Gemini API with JSON mode if key available, else falls back to smart parser."""
    key = api_key or get_gemini_api_key()
    if key:
        sys_prompt = """Bạn là trợ lý AI thông minh của phòng khám Nha Khoa NiBi (BS. Thắng).
Nhiệm vụ của bạn là đọc một câu mô tả tự nhiên của bác sĩ hoặc lễ tân và bóc tách thành đối tượng JSON chính xác để điền vào hồ sơ bệnh nhân mới.
Trả về định dạng JSON thuần túy với các trường:
{
  "name": "Họ và tên bệnh nhân (viết hoa chữ cái đầu)",
  "phone": "Số điện thoại nếu có (chuỗi)",
  "yob": Năm sinh (số nguyên 4 chữ số hoặc null),
  "age": Tuổi (số nguyên hoặc null),
  "address": "Địa chỉ / quê quán (ví dụ: Xí Nghiệp, Xuân Vũ, Phú Lăng, Ninh Vân, Phố Dâu...)",
  "branch": "Cơ sở: Ninh Vân, Phố Dâu hoặc Cửu Yên (mặc định Ninh Vân)",
  "category": "Nhóm dịch vụ: Tổng quát, Nhổ răng, Điều trị tủy, Răng giả, Chỉnh nha, Implant",
  "procedure": "Nội dung thủ thuật điều trị ngắn gọn",
  "tooth": "Vị trí răng nếu có (ví dụ R48, R16)",
  "cost": Tổng chi phí tính bằng VNĐ (số nguyên, ví dụ 800k = 800000),
  "paid": Số tiền đã thanh toán bằng VNĐ (số nguyên, ví dụ 500k = 500000),
  "method": "Hình thức: Tiền mặt hoặc Chuyển khoản (mặc định Tiền mặt)",
  "next_date": "Ngày hẹn tái khám định dạng YYYY-MM-DD nếu có, không có thì để rỗng",
  "notes": "Ghi chú điều trị nếu có"
}
"""
        response_text = call_gemini_api(text, system_instruction=sys_prompt, api_key=key, json_mode=True)
        if response_text:
            try:
                # Strip markdown code blocks if any
                clean_json = response_text.strip()
                if clean_json.startswith("```json"):
                    clean_json = clean_json[7:]
                if clean_json.startswith("```"):
                    clean_json = clean_json[3:]
                if clean_json.endswith("```"):
                    clean_json = clean_json[:-3]
                data = json.loads(clean_json.strip())
                if isinstance(data, dict) and data.get("name"):
                    return data
            except Exception as e:
                print(f"JSON Parse Error from Gemini: {e}")

    # Fallback rule-based
    return parse_intake_fallback(text)

def lookup_patient_records(conn, query: str):
    """Tìm kiếm hồ sơ bệnh nhân theo tên, số điện thoại hoặc mã bệnh nhân."""
    q = query.strip()
    # 1. Phone number match
    phone_m = re.search(r'(0[3|5|7|8|9]\d{7,8})', q)
    if phone_m:
        return conn.execute(
            """SELECT id, code, name, phone, phone_note, yob, address, branch, notes 
               FROM patients 
               WHERE phone LIKE ? 
               ORDER BY id DESC LIMIT 5""",
            (f"%{phone_m.group(1)}%",)
        ).fetchall()

    # 2. Patient code match
    code_m = re.search(r'(BN-\d+)', q, re.IGNORECASE)
    if code_m:
        return conn.execute(
            """SELECT id, code, name, phone, phone_note, yob, address, branch, notes 
               FROM patients 
               WHERE code LIKE ? 
               ORDER BY id DESC LIMIT 5""",
            (f"%{code_m.group(1)}%",)
        ).fetchall()

    lower_q = q.lower()
    patient_triggers = [
        "bệnh nhân", "bn", "khách", "hồ sơ", "thông tin", "tìm", "tra cứu", 
        "sđt", "số điện thoại", "điều trị", "anh", "chị", "cô", "chú", "bác", "em"
    ]
    is_patient_query = any(t in lower_q for t in patient_triggers)

    cleaned = re.sub(
        r'(?:thông tin|hồ sơ|lịch sử|tìm|tra cứu|cho tôi|hỏi về|xem|bệnh nhân|bn|khách hàng|khách|anh|chị|cô|chú|bác|em|ở đâu|bao nhiêu|nợ|sđt|số điện thoại|của|\?|:)', 
        ' ', 
        lower_q
    ).strip()

    if cleaned and len(cleaned) >= 2:
        norm = strip_accents(cleaned)
        # Search by exact name substring or normalized search_text
        rows = conn.execute(
            """SELECT id, code, name, phone, phone_note, yob, address, branch, notes 
               FROM patients 
               WHERE name LIKE ? OR search_text LIKE ?
               ORDER BY (CASE WHEN name LIKE ? THEN 0 ELSE 1 END), id DESC
               LIMIT 5""",
            (f"%{cleaned}%", f"%{norm}%", f"%{cleaned}%")
        ).fetchall()
        if rows:
            return rows

        words = [w for w in norm.split() if len(w) >= 2]
        if words:
            like_sql = " AND ".join(["search_text LIKE ?" for _ in words])
            params = [f"%{w}%" for w in words]
            rows = conn.execute(
                f"""SELECT id, code, name, phone, phone_note, yob, address, branch, notes 
                   FROM patients 
                   WHERE {like_sql} 
                   ORDER BY id DESC LIMIT 5""",
                params
            ).fetchall()
            if rows:
                return rows

    return []

def format_single_patient_card(conn, p, today_str: str) -> str:
    """Định dạng thẻ chi tiết bệnh nhân kèm lịch sử điều trị và lịch hẹn."""
    p_id = p[0]
    p_code = p[1]
    p_name = p[2]
    p_phone = p[3]
    p_phone_note = p[4]
    p_yob = p[5]
    p_address = p[6]
    p_branch = p[7]
    p_notes = p[8]

    current_year = dt.date.today().year
    age_str = f" ({current_year - p_yob} tuổi)" if p_yob else ""
    yob_str = f"SN {p_yob}{age_str}" if p_yob else "Chưa có năm sinh"
    phone_display = p_phone or "Chưa có SĐT"
    if p_phone_note:
        phone_display += f" ({p_phone_note})"

    tp_rows = conn.execute("""
        WITH tp AS (
            SELECT t.id, t.procedure, t.cost, t.visit_date, t.dentist,
                   COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id = t.id), 0) as paid
            FROM treatments t
            WHERE t.patient_id = ?
            ORDER BY t.visit_date DESC, t.id DESC
        )
        SELECT procedure, cost, paid, MAX(cost - paid, 0) as debt, visit_date, dentist FROM tp
    """, (p_id,)).fetchall()

    total_cost = sum(r[1] for r in tp_rows)
    total_paid = sum(r[2] for r in tp_rows)
    total_debt = sum(r[3] for r in tp_rows)

    appt_row = conn.execute("""
        SELECT appt_date, appt_time, reason, branch FROM appointments
        WHERE patient_id = ? AND appt_date >= ?
        ORDER BY appt_date ASC, appt_time ASC LIMIT 1
    """, (p_id, today_str)).fetchone()

    lines = [
        f"👤 **Hồ Sơ Bệnh Nhân: {p_name}** (`{p_code}`)",
        f"- 📞 **SĐT:** `{phone_display}`",
        f"- 🎂 **Năm sinh:** {yob_str}",
        f"- 📍 **Địa chỉ:** {p_address or 'Chưa cập nhật'} (Cơ sở tiếp đón: **{p_branch}**)",
        f"- 💰 **Tài chính:** Tổng chi phí `{total_cost/1000:,.1f}K` | Đã trả `{total_paid/1000:,.1f}K` | **Còn nợ: `{total_debt/1000:,.1f}K`**"
    ]
    if tp_rows:
        lines.append("- 🦷 **Lịch sử điều trị:**")
        for r in tp_rows[:4]:
            cost_k = f"{r[1]/1000:,.0f}K"
            debt_k = f"(còn nợ {r[3]/1000:,.0f}K)" if r[3] > 0 else "(đã trả đủ)"
            lines.append(f"  • *{r[4] or 'Mới'}:* {r[0]} (BS. {r[5] or 'Thắng'}) — {cost_k} {debt_k}")
        if len(tp_rows) > 4:
            lines.append(f"  *(và {len(tp_rows) - 4} đợt điều trị trước đó...)*")
    else:
        lines.append("- 🦷 Chưa có lịch sử điều trị.")

    if appt_row:
        lines.append(f"- 📅 **Lịch hẹn tiếp theo:** Ngày `{appt_row[0]}` ({appt_row[1] or 'trong ngày'}) — *{appt_row[2]}* (CS {appt_row[3]})")
    if p_notes:
        lines.append(f"- 📝 **Ghi chú:** {p_notes}")

    return "\n".join(lines)

def answer_clinic_query(message: str, api_key: str = "", is_admin: bool = False) -> dict:
    """Answers clinic queries using real database context with RBAC security and patient search."""
    today_str = dt.date.today().isoformat()
    current_month_str = dt.date.today().strftime("%Y-%m")
    lower = message.lower()

    # 1. PHÂN QUYỀN DOANH THU: Chặn tài khoản nhân viên thường hỏi về doanh thu
    revenue_keywords = ["doanh thu", "thu nhập", "tiền thu", "doanh số", "tổng thu", "lợi nhuận", "lãi", "tháng này thu"]
    if not is_admin and any(k in lower for k in revenue_keywords):
        return {
            "reply": (
                "🔒 **Bảo Mật Tài Chính:**\n"
                "Thông tin doanh thu và tài chính phòng khám là dữ liệu nội bộ được bảo mật, **chỉ dành riêng cho tài khoản Admin (Bác sĩ Thắng)**.\n\n"
                "💡 *Bạn có thể tra cứu thông tin hồ sơ bệnh nhân, tìm số điện thoại, xem tiền nợ của bệnh nhân hoặc lịch hẹn tái khám.*"
            ),
            "source": "rbac_restricted"
        }

    # Fetch live database snapshot
    with db() as conn:
        conn.row_factory = None
        total_patients = conn.execute("SELECT COUNT(id) FROM patients").fetchone()[0]

        # Monthly revenue (chỉ tính nếu là admin)
        month_total, month_cash, month_transfer = 0, 0, 0
        if is_admin:
            rev_row = conn.execute("""
                SELECT 
                    COALESCE(SUM(amount), 0),
                    COALESCE(SUM(CASE WHEN method LIKE '%Tiền mặt%' THEN amount ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN method LIKE '%Chuyển khoản%' THEN amount ELSE 0 END), 0)
                FROM payments
                WHERE paid_on LIKE ?
            """, (current_month_str + '%',)).fetchone()
            month_total, month_cash, month_transfer = rev_row

        # Total debt
        debt_total = conn.execute("""
            WITH tp AS (
                SELECT t.cost, COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id = t.id), 0) as paid
                FROM treatments t
            )
            SELECT COALESCE(SUM(MAX(cost - paid, 0)), 0) FROM tp WHERE cost > paid
        """).fetchone()[0]

        debt_patients_count = conn.execute("""
            WITH tp AS (
                SELECT t.patient_id, t.cost, COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id = t.id), 0) as paid
                FROM treatments t
            )
            SELECT COUNT(DISTINCT patient_id) FROM tp WHERE cost > paid
        """).fetchone()[0]

        # Top 5 debtors
        top_debtors = conn.execute("""
            WITH tp AS (
                SELECT t.patient_id, t.cost, COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id = t.id), 0) as paid
                FROM treatments t
            )
            SELECT p.name, p.phone, p.branch, SUM(MAX(tp.cost - tp.paid, 0)) as debt
            FROM patients p
            JOIN tp ON tp.patient_id = p.id
            WHERE tp.cost > tp.paid
            GROUP BY p.id
            ORDER BY debt DESC
            LIMIT 5
        """).fetchall()

        # Appointments today
        appts_today = conn.execute("""
            SELECT p.name, p.phone, a.appt_time, a.reason, COALESCE(a.branch, p.branch)
            FROM appointments a
            JOIN patients p ON p.id = a.patient_id
            WHERE a.appt_date = ?
            ORDER BY a.appt_time ASC
        """, (today_str,)).fetchall()

        # Check for Patient Search
        matched_patients = lookup_patient_records(conn, message)
        patient_context = ""
        if matched_patients:
            if len(matched_patients) == 1:
                patient_context = format_single_patient_card(conn, matched_patients[0], today_str)
            else:
                card_list = []
                for p in matched_patients:
                    p_phone = p[3] or "Không có SĐT"
                    p_addr = p[6] or "Chưa có địa chỉ"
                    card_list.append(f"- **{p[2]}** (`{p[1]}`): SĐT `{p_phone}` — {p_addr} (CS {p[7]})")
                patient_context = (
                    f"🔍 **Tìm thấy {len(matched_patients)} bệnh nhân phù hợp:**\n"
                    + "\n".join(card_list)
                    + "\n\n💡 *Bác sĩ hãy hỏi cụ thể hơn (ví dụ: 'thông tin của " + matched_patients[0][2] + "') để xem đầy đủ hồ sơ và tiền nợ.*"
                )

    # 2. Xử lý qua Google Gemini API nếu có key
    key = api_key or get_gemini_api_key()
    if key:
        db_context_parts = [
            f"DỮ LIỆU THỜI GIAN THỰC NHA KHOA NIBI (Hôm nay: {today_str}):",
            f"- Tổng số bệnh nhân: {total_patients} người"
        ]
        if is_admin:
            db_context_parts.append(
                f"- Doanh thu tháng {current_month_str}: {month_total/1000:,.1f}K ({month_total:,.0f} VNĐ) [Tiền mặt: {month_cash/1000:,.1f}K, Chuyển khoản: {month_transfer/1000:,.1f}K]"
            )
        else:
            db_context_parts.append("- (Lưu ý bảo mật: Tài khoản người dùng không phải Admin, KHÔNG có quyền truy cập doanh thu)")

        appts_summary = ', '.join(f"{a[0]} ({a[2] or 'cả ngày'} - {a[3]})" for a in appts_today) if appts_today else 'Không có lịch hẹn'
        db_context_parts.extend([
            f"- Tổng công nợ: {debt_total/1000:,.1f}K của {debt_patients_count} bệnh nhân",
            f"- Top nợ: {', '.join(f'{d[0]} ({d[3]/1000:,.0f}K)' for d in top_debtors)}",
            f"- Lịch hẹn hôm nay ({len(appts_today)} ca): {appts_summary}"
        ])
        if patient_context:
            db_context_parts.append(f"\nKẾT QUẢ TRA CỨU HỒ SƠ BỆNH NHÂN TRONG CSDL:\n{patient_context}")

        db_context = "\n".join(db_context_parts)
        sys_prompt = f"""Bạn là Trợ Lý AI Chuyên Nghiệp của Nha Khoa NiBi (BS. Thắng).
Dữ liệu thời gian thực từ CSDL:
{db_context}
Quy tắc:
1. Trả lời súc tích, chính xác, lịch sự, đúng sự thật.
2. {'Nếu người dùng hỏi về doanh thu thì trả lời đầy đủ.' if is_admin else 'Người dùng là nhân viên: TUYỆT ĐỐI từ chối cung cấp bất kỳ số liệu doanh thu nào.'}
3. Nếu người dùng hỏi về thông tin bệnh nhân, sử dụng thông tin bệnh nhân đã tìm được ở trên để trả lời đầy đủ.
"""
        reply = call_gemini_api(message, system_instruction=sys_prompt, api_key=key)
        if reply:
            return {"reply": reply, "source": "gemini"}

    # 3. Fallback Local NLP
    # Nếu có kết quả tra cứu bệnh nhân
    if patient_context:
        return {"reply": patient_context, "source": "patient_lookup"}

    if is_admin and any(k in lower for k in revenue_keywords):
        reply = (
            f"📊 **Báo Cáo Doanh Thu Tháng {current_month_str}:**\n"
            f"- **Tổng thực thu:** `{month_total/1000:,.1f}K` ({month_total:,.0f} VNĐ)\n"
            f"- **Tiền mặt:** `{month_cash/1000:,.1f}K`\n"
            f"- **Chuyển khoản:** `{month_transfer/1000:,.1f}K`\n"
            f"*(Đồng bộ trực tiếp từ sổ thu ngân 3 cơ sở Ninh Vân, Phố Dâu, Cửu Yên)*"
        )
    elif any(k in lower for k in ["nợ", "công nợ", "ai nợ"]):
        debtors_text = "\n".join([f"{i+1}. **{d[0]}** ({d[2]} - SĐT: {d[1] or 'Chưa có'}): nợ `{d[3]/1000:,.1f}K`" for i, d in enumerate(top_debtors)])
        reply = (
            f"💰 **Tình Hình Công Nợ Phòng Khám:**\n"
            f"- **Tổng tiền còn nợ:** `{debt_total/1000:,.1f}K` ({debt_total:,.0f} VNĐ)\n"
            f"- **Số bệnh nhân nợ:** `{debt_patients_count}` người\n\n"
            f"**Top bệnh nhân nợ nhiều nhất:**\n{debtors_text}"
        )
    elif any(k in lower for k in ["hôm nay", "lịch hẹn", "hẹn"]):
        if appts_today:
            appt_list = "\n".join([f"- **{a[0]}** ({a[1] or 'Không SĐT'}): {a[2] or 'Trong ngày'} - {a[3]} (CS {a[4]})" for a in appts_today])
            reply = f"📅 **Hôm nay ({today_str}) có {len(appts_today)} bệnh nhân hẹn:**\n{appt_list}"
        else:
            reply = f"📅 **Hôm nay ({today_str}) không có bệnh nhân nào có lịch hẹn.** Bạn có thể xem lịch hẹn cả tháng trong tab *Lịch hẹn & Tái khám*."
    else:
        role_note = "Bác sĩ Thắng" if is_admin else "Nhân viên"
        rev_bullet = f"- Doanh thu tháng này đạt: `{month_total/1000:,.1f}K`.\n" if is_admin else ""
        reply = (
            f"🦷 **Nha Khoa NiBi xin chào {role_note}!**\n"
            f"- Hệ thống đang theo dõi **{total_patients}** bệnh nhân trên 3 cơ sở.\n"
            f"{rev_bullet}"
            f"- Tổng công nợ hiện tại: `{debt_total/1000:,.1f}K` ({debt_patients_count} người).\n\n"
            f"💡 *Bạn có thể hỏi tôi:*\n"
            f"- 👤 *'Thông tin bệnh nhân [Tên/SĐT]'* để xem hồ sơ và lịch sử điều trị\n"
            f"- 📅 *'Lịch hẹn hôm nay'* để xem danh sách bệnh nhân hẹn\n"
            f"- 💰 *'Ai đang nợ nhiều nhất?'* để xem công nợ"
            + (f"\n- 📊 *'Doanh thu tháng này'* để xem báo cáo thu chi" if is_admin else "")
        )

    return {"reply": reply, "source": "local_nlp"}

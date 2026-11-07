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

DEFAULT_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

def get_gemini_api_key() -> str:
    # 1. Environment variable
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    # 2. Database settings
    try:
        with db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'gemini_api_key'").fetchone()
            if row and row[0] and row[0].strip():
                return row[0].strip()
    except Exception:
        pass
    return ""

def call_gemini_api(prompt: str, system_instruction: str = "", api_key: str = "", json_mode: bool = False) -> str:
    if not api_key:
        api_key = get_gemini_api_key()
    if not api_key:
        return ""

    candidate_models = ["gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-flash-latest"]
    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "temperature": 0.3,
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
            with urllib.request.urlopen(req, timeout=12) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                candidates = res_json.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
        except Exception as e:
            continue
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
    res["branch"] = "Ninh Vân"

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
  "address": "Địa chỉ / quê quán (ví dụ: Xí Nghiệp, Xuân Vũ, Phú Lăng, Ninh Vân...)",
  "branch": "Cơ sở: Ninh Vân",
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
                    fb = parse_intake_fallback(text)
                    if not data.get("next_date") and fb.get("next_date"):
                        data["next_date"] = fb["next_date"]
                    if not data.get("address") and fb.get("address"):
                        data["address"] = fb["address"]
                    if not data.get("phone") and fb.get("phone"):
                        data["phone"] = fb["phone"]
                    return data
            except Exception as e:
                print(f"JSON Parse Error from Gemini: {e}")

    # Fallback rule-based
    return parse_intake_fallback(text)

STOPWORDS = {
    'tra', 'cuu', 'tim', 'xem', 'cho', 'toi', 'hoi', 've', 'thong', 'tin', 'ho', 'so', 'lich', 'su',
    'benh', 'nhan', 'khach', 'hang', 'anh', 'chi', 'co', 'chu', 'bac', 'em', 'cu', 'moi', 'o', 'dau',
    'bao', 'nhieu', 'tien', 'no', 'sdt', 'so', 'dien', 'thoai', 'dieu', 'tri', 'nhung', 'gi', 'con',
    'khong', 'chua', 'co', 'nay', 'do', 'duoc', 'giup', 'voi', 'rang', 'kham',
    'thu', 'doanh', 'chi', 'phi', 'tong', 'thang', 'nam', 'ngay', 'tuan', 'the', 'nao', 'sao', 'lai',
    'loi', 'hen', 'lich', 'gio', 'buoi', 'sang', 'chieu', 'toi', 'trua', 'hai', 'ba', 'tu', 'bay'
}

def lookup_patient_records(conn, query: str):
    """Tìm kiếm hồ sơ bệnh nhân trong toàn bộ cơ sở dữ liệu theo số điện thoại, mã bệnh nhân hoặc họ tên (toàn bộ 300+ bệnh nhân)."""
    q = query.strip()
    norm_q = strip_accents(q)

    # Nếu câu hỏi thuần túy về doanh thu/tài chính chung mà không hỏi về bệnh nhân cụ thể, bỏ qua tìm kiếm bệnh nhân
    revenue_intent = any(k in q.lower() for k in ["doanh thu", "thu nhập", "doanh số", "lợi nhuận", "tiền thu tháng", "tháng này thu"])
    patient_intent = any(k in norm_q for k in ["benh nhan", "bn", "khach", "ho so", "benh an", "sdt", "anh", "chi", "co", "chu", "bac", "em", "ten"])
    if revenue_intent and not patient_intent:
        return []

    # Tải toàn bộ danh sách bệnh nhân từ CSDL (tốc độ cực nhanh < 1ms với SQLite)
    all_patients = conn.execute(
        "SELECT id, code, name, phone, phone_note, yob, address, branch, notes, search_text FROM patients"
    ).fetchall()
    if not all_patients:
        return []

    # 1. Phone number match
    phone_m = re.search(r'(0\d{8,10})', q)
    if phone_m:
        p_matches = [p for p in all_patients if p[3] and phone_m.group(1) in p[3]]
        if p_matches:
            return p_matches[:5]

    # 2. Patient code match (e.g. BN00184, BN-00184, BN184)
    code_m = re.search(r'bn[-\s]?0*(\d+)', norm_q)
    if code_m:
        target_num = int(code_m.group(1))
        p_matches = [
            p for p in all_patients 
            if p[1] and re.search(r'\d+', p[1]) and int(re.search(r'\d+', p[1]).group()) == target_num
        ]
        if p_matches:
            return p_matches[:5]

    # 3. Intelligent Match against patient names (tìm kiếm xuyên suốt toàn bộ bệnh nhân cũ & mới)
    matches = []
    for p in all_patients:
        p_name = p[2]
        p_norm = strip_accents(p_name).strip()
        p_words = p_norm.split()

        # Score 100: Khớp hoàn toàn họ tên đầy đủ trong câu hỏi (ví dụ: 'đỗ đình huy' trong 'tra bệnh nhân cũ đỗ đình huy')
        if p_norm and p_norm in norm_q and not all(w in STOPWORDS for w in p_words):
            matches.append((100, len(p_norm), p))
            continue

        # Score 80: Khớp 2 từ cuối của tên (ví dụ: 'đình huy', 'thị vui', 'băng nhi')
        if len(p_words) >= 2:
            w1, w2 = p_words[-2], p_words[-1]
            if w1 not in STOPWORDS and w2 not in STOPWORDS:
                last_two = f"{w1} {w2}"
                if last_two in norm_q:
                    matches.append((80, len(last_two), p))
                    continue

        # Score 50: Khớp tên riêng (từ cuối) - chỉ áp dụng khi có ngữ cảnh hỏi bệnh nhân hoặc từ không thuộc từ dừng
        if len(p_words) >= 1:
            given = p_words[-1]
            if given not in STOPWORDS and len(given) >= 2:
                if re.search(r'\b' + re.escape(given) + r'\b', norm_q):
                    # Chỉ chấp nhận khớp 1 từ đơn nếu có dấu hiệu tìm người hoặc từ khóa đủ rõ
                    if patient_intent or len(given) >= 4:
                        matches.append((50, len(given), p))
                    continue

    if not matches and patient_intent:
        # Fallback substring search trên chuỗi đã lọc từ dừng
        cleaned = re.sub(
            r'(?:thong tin|ho so|lich su|tim|tra cuu|cho toi|hoi ve|xem|benh nhan|bn|khach hang|khach|anh|chi|co|chu|bac|em|cu|moi|o dau|bao nhieu|no|sdt|so dien thoai|cua|\?|:)', 
            ' ', 
            norm_q
        ).strip()
        if len(cleaned) >= 2:
            for p in all_patients:
                p_norm = strip_accents(p[2]).strip()
                if cleaned in p_norm or (p[9] and cleaned in p[9]):
                    matches.append((60, len(cleaned), p))

    if not matches:
        return []

    # Giữ lại nhóm điểm cao nhất để lọc bỏ các kết quả nhiễu
    highest_score = max(m[0] for m in matches)
    filtered = [m for m in matches if m[0] == highest_score]
    filtered.sort(key=lambda x: x[1], reverse=True)
    return [m[2] for m in filtered[:5]]

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

    p_extra = conn.execute("SELECT medical_history FROM patients WHERE id = ?", (p_id,)).fetchone()
    med_hist = p_extra[0] if p_extra and p_extra[0] else ""

    tp_rows = conn.execute("""
        WITH tp AS (
            SELECT t.id, t.procedure, t.tooth, t.cost, t.visit_date, t.dentist,
                   COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id = t.id), 0) as paid
            FROM treatments t
            WHERE t.patient_id = ?
            ORDER BY t.visit_date DESC, t.id DESC
        )
        SELECT procedure, tooth, cost, paid, MAX(cost - paid, 0) as debt, visit_date, dentist FROM tp
    """, (p_id,)).fetchall()

    total_cost = sum(r[2] for r in tp_rows)
    total_paid = sum(r[3] for r in tp_rows)
    total_debt = sum(r[4] for r in tp_rows)

    appt_row = conn.execute("""
        SELECT appt_date, appt_time, reason, branch FROM appointments
        WHERE patient_id = ? AND appt_date >= ?
        ORDER BY appt_date ASC, appt_time ASC LIMIT 1
    """, (p_id, today_str)).fetchone()

    lines = [
        f"👤 **HỒ SƠ BỆNH ÁN: {p_name.upper()}** (`{p_code}`)",
        f"- 📞 **SĐT:** `{phone_display}`",
        f"- 🎂 **Năm sinh:** {yob_str}",
        f"- 📍 **Địa chỉ:** {p_address or 'Chưa cập nhật'} (Cơ sở tiếp đón: **{p_branch}**)",
        f"- 💰 **Tài chính:** Tổng chi phí `{total_cost/1000:,.1f}K` | Đã trả `{total_paid/1000:,.1f}K` | **Còn nợ: `{total_debt/1000:,.1f}K`**"
    ]
    if med_hist:
        lines.append(f"- 🏥 **Tiền sử bệnh:** {med_hist}")
    if tp_rows:
        lines.append(f"- 🦷 **Lịch sử điều trị ({len(tp_rows)} đợt):**")
        for r in tp_rows[:12]:
            tooth_str = f" [R{r[1]}]" if r[1] else ""
            cost_k = f"{r[2]/1000:,.0f}K"
            debt_k = f"(còn nợ {r[4]/1000:,.0f}K)" if r[4] > 0 else "(đã trả đủ)"
            lines.append(f"  • *{r[5] or 'Mới'}:* **{r[0]}**{tooth_str} (BS. {r[6] or 'Thắng'}) — {cost_k} {debt_k}")
        if len(tp_rows) > 12:
            lines.append(f"  *(và {len(tp_rows) - 12} đợt điều trị trước đó nữa...)*")
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
        else:
            # Nếu người dùng hỏi tổng quát về danh sách bệnh nhân hoặc lịch sử điều trị
            general_patient_triggers = [
                "danh sách bệnh nhân", "hồ sơ bệnh nhân", "hồ sơ", "lịch sử điều trị", 
                "bệnh nhân điều trị", "truy xuất", "tra cứu", "các bệnh nhân", 
                "bệnh nhân mới", "ca điều trị", "điều trị bệnh nhân", "thông tin hồ sơ",
                "danh sách", "bệnh án", "lịch sử", "truy suất"
            ]
            if any(t in lower for t in general_patient_triggers):
                recent_patients = conn.execute("""
                    SELECT p.id, p.code, p.name, p.phone, p.address, p.branch
                    FROM patients p
                    ORDER BY p.id DESC
                    LIMIT 8
                """).fetchall()

                recent_treatments = conn.execute("""
                    SELECT t.visit_date, p.name, p.code, t.procedure, t.tooth, t.cost, t.dentist
                    FROM treatments t
                    JOIN patients p ON p.id = t.patient_id
                    ORDER BY t.visit_date DESC, t.id DESC
                    LIMIT 6
                """).fetchall()

                p_list_lines = []
                for p in recent_patients:
                    sdt = p[3] or "Chưa có SĐT"
                    addr = f" — {p[4]}" if p[4] else ""
                    p_list_lines.append(f"- **{p[2]}** (`{p[1]}`): SĐT `{sdt}`{addr} (CS **{p[5]}**)")

                t_list_lines = []
                for t in recent_treatments:
                    tooth_str = f" [R{t[4]}]" if t[4] else ""
                    t_list_lines.append(f"- *{t[0] or 'Mới'}:* **{t[1]}** (`{t[2]}`) — {t[3]}{tooth_str} (`{t[5]/1000:,.0f}K`, BS. {t[6] or 'Thắng'})")

                p_sample_1 = recent_patients[0][2] if recent_patients else "Nguyễn Văn A"
                p_sample_2 = recent_patients[1][2] if len(recent_patients) > 1 else p_sample_1
                patient_context = (
                    f"📋 **DANH SÁCH BỆNH NHÂN TIẾP ĐÓN GẦN ĐÂY (Toàn hệ thống có {total_patients} bệnh nhân):**\n"
                    + "\n".join(p_list_lines)
                    + f"\n\n🦷 **CÁC ĐỢT ĐIỀU TRỊ THỰC HIỆN GẦN ĐÂY NHẤT:**\n"
                    + "\n".join(t_list_lines)
                    + f"\n\n💡 *Hệ thống lưu trữ toàn bộ dữ liệu của tất cả **{total_patients}** bệnh nhân cũ và mới. Bạn có thể tra cứu bất kỳ ai (Ví dụ: 'hồ sơ của {p_sample_1}', 'bệnh nhân cũ Đỗ Đình Huy', 'lịch sử điều trị của {p_sample_2}').*"
                )

    # 2. Xử lý qua Google Gemini API nếu có key
    key = api_key or get_gemini_api_key()
    if key:
        db_context_parts = [
            f"DỮ LIỆU THỜI GIAN THỰC NHA KHOA NIBI (Hôm nay: {today_str}):",
            f"- Toàn hệ thống có: {total_patients} bệnh nhân (cả cũ và mới)"
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
            db_context_parts.append(f"\nKẾT QUẢ TRA CỨU HỒ SƠ BỆNH NHÂN TRONG CSDL (Toàn bộ {total_patients} bệnh nhân):\n{patient_context}")

        db_context = "\n".join(db_context_parts)
        sys_prompt = f"""Bạn là Trợ Lý AI Chuyên Nghiệp của Nha Khoa NiBi (BS. Thắng).
Dữ liệu thời gian thực từ CSDL:
{db_context}
Quy tắc:
1. Trả lời súc tích, chính xác, lịch sự, đúng sự thật.
2. {'Nếu người dùng hỏi về doanh thu thì trả lời đầy đủ.' if is_admin else 'Người dùng là nhân viên: TUYỆT ĐỐI từ chối cung cấp bất kỳ số liệu doanh thu nào.'}
3. Hệ thống CSDL có toàn bộ {total_patients} bệnh nhân cũ và mới. Khi người dùng tra cứu thông tin bất kỳ bệnh nhân nào (kể cả bệnh nhân cũ), hãy dựa vào kết quả tra cứu CSDL ở trên để trả lời đầy đủ, chi tiết (họ tên, sđt, quê quán, lịch sử điều trị, tình hình nợ).
4. Tuyệt đối không nói 'chưa có sẵn trong danh sách 8 hồ sơ' nếu hồ sơ bệnh nhân đó đã được tìm thấy trong kết quả tra cứu CSDL.
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
            f"*(Đồng bộ trực tiếp từ sổ thu ngân hệ thống)*"
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


def parse_supply_fallback(text: str) -> dict:
    """Smart Vietnamese rule-based extractor for dental supplies and medicines."""
    raw = text.strip()
    res = {
        "name": "",
        "category": "Vật liệu tiêu hao",
        "unit": "Hộp",
        "quantity": 1,
        "min_threshold": 5,
        "expiry_date": None,
        "unit_price": 0,
        "supplier": "",
        "notes": "",
    }
    if not raw:
        return res

    lower = raw.lower()

    # 1. Category detection
    if any(k in lower for k in ["tê", "thuốc", "kháng sinh", "paracetamol", "amox", "lidocain", "artin", "mepivastesin", "septanest", "adrenalin", "panadol", "efferalgan"]):
        res["category"] = "Thuốc & Tê"
    elif any(k in lower for k in ["composite", "trám", "keo", "bond", "axit", "etching", "fuji", "gutta", "nong", "tủy", "chất hàn"]):
        res["category"] = "Trám răng & Điều trị"
    elif any(k in lower for k in ["mắc cài", "dây cung", "thun", "chỉnh nha", "kìm bẻ", "niềng", "bracket"]):
        res["category"] = "Chỉnh nha"
    elif any(k in lower for k in ["labo", "răng giả", "khay lấy dấu", "alginate", "thạch cao", "sáp", "sứ", "răng nhựa"]):
        res["category"] = "Răng giả & Labo"
    elif any(k in lower for k in ["kìm", "gắp", "thám châm", "gương", "tay khoan", "mũi khoan", "đèn quang", "đèn trùng hợp", "máy"]):
        res["category"] = "Dụng cụ & Khác"
    else:
        res["category"] = "Vật liệu tiêu hao"

    # 2. Unit detection
    for u in ["Hộp", "Ống", "Tuýp", "Lọ", "Gói", "Chiếc", "Bộ", "Cuộn", "Cái", "Vỉ", "Bình"]:
        if re.search(r'\b' + re.escape(u.lower()) + r'\b', lower):
            res["unit"] = u
            break

    # 3. Quantity detection
    m_qty2 = re.search(r'(\d+)\s*(?:hộp|ống|tuýp|lọ|gói|chiếc|bộ|cuộn|cái|vỉ|bình)', lower)
    if m_qty2:
        try:
            res["quantity"] = int(m_qty2.group(1))
        except ValueError:
            pass
    else:
        m_qty = re.search(r'(?:nhập|số lượng|sl|mua|có)?\s*(\d+)', lower)
        if m_qty:
            try:
                q = int(m_qty.group(1))
                if 0 < q < 10000:
                    res["quantity"] = q
            except ValueError:
                pass

    # 4. Expiry date detection
    text_for_price = lower
    m_date = re.search(r'(?:date|hạn|hsd|hết hạn|exp|đến)?\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})', lower)
    if m_date:
        d, m, y = int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3))
        res["expiry_date"] = f"{y:04d}-{m:02d}-{d:02d}"
        text_for_price = text_for_price[:m_date.start()] + " " + text_for_price[m_date.end():]
    else:
        m_my = re.search(r'(?:date|hạn|hsd|hết hạn|exp|tháng)\s*(\d{1,2})[/-](\d{4})', lower)
        if m_my:
            m, y = int(m_my.group(1)), int(m_my.group(2))
            res["expiry_date"] = f"{y:04d}-{m:02d}-28"
            text_for_price = text_for_price[:m_my.start()] + " " + text_for_price[m_my.end():]
        else:
            m_yr = re.search(r'(?:date|hạn|hsd|hết hạn|exp|năm)\s*(20\d{2})', lower)
            if m_yr:
                res["expiry_date"] = f"{m_yr.group(1)}-12-31"
                text_for_price = text_for_price[:m_yr.start()] + " " + text_for_price[m_yr.end():]

    # 5. Price detection
    m_k = re.search(r'(?:giá|đơn giá|giá nhập)?\s*(\d+(?:[.,]\d+)?)\s*(?:k|nghìn|ngàn)\b', text_for_price)
    if m_k:
        val = float(m_k.group(1).replace(",", "."))
        res["unit_price"] = int(val * 1000)
    else:
        m_full = re.search(r'(?:giá|đơn giá)?\s*(\d{1,3}(?:\.\d{3})+|\d{5,9})\s*(?:đ|vnd|vnđ)?\b', text_for_price)
        if m_full:
            clean_digits = re.sub(r'\D', '', m_full.group(1))
            if clean_digits:
                val = int(clean_digits)
                if val >= 500:
                    res["unit_price"] = val

    # 6. Supplier detection
    m_sup = re.search(r'(?:ncc|nhà cung cấp|cty|công ty|mua của|từ)\s+([^,\n.]+)', raw, re.IGNORECASE)
    if m_sup:
        res["supplier"] = m_sup.group(1).strip()

    # 7. Name detection
    clean_name = re.sub(r'^(nhập|mua|thêm|vừa mua|lấy|bổ sung)\s+', '', raw, flags=re.IGNORECASE)
    clean_name = re.sub(r'\b\d+\s*(hộp|ống|tuýp|lọ|gói|chiếc|bộ|cuộn|cái|vỉ|bình)\b', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'(?:giá|đơn giá)\s*[:\s]*\d+.*?(k|nghìn|ngàn|vnd|vnđ|đ|\b)', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'\b\d+\s*(?:k|nghìn|ngàn)\b', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'(?:date|hạn|hsd|hết hạn|exp)\s*[:\s]*[\d\/\-]+', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'(?:ncc|nhà cung cấp|cty|công ty|mua của|từ)\s+[^,\n.]+', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r'[,\.;]+', ' ', clean_name).strip()
    clean_name = re.sub(r'\s+', ' ', clean_name)
    if len(clean_name) >= 2:
        res["name"] = clean_name[:100].strip()
    else:
        res["name"] = raw[:80].strip()

    return res


def parse_supply_with_ai(text: str, api_key: str = "") -> dict:
    """Uses Gemini API with JSON structured output, falls back to rule-based regex."""
    fallback = parse_supply_fallback(text)
    system_instruction = (
        "Bạn là trợ lý thông minh của Nha Khoa NiBi - Bác sĩ Thắng. "
        "Nhiệm vụ của bạn là bóc tách thông tin vật tư, thuốc, dụng cụ y tế từ văn bản tiếng Việt "
        "thành JSON có cấu trúc chính xác.\n"
        "Các trường JSON yêu cầu:\n"
        "- name: tên thuốc / vật tư chuẩn hóa, viết hoa chữ cái đầu (VD: 'Thuốc tê Pháp Septanest 4%')\n"
        "- category: một trong ['Vật liệu tiêu hao', 'Thuốc & Tê', 'Trám răng & Điều trị', 'Chỉnh nha', 'Răng giả & Labo', 'Dụng cụ & Khác']\n"
        "- unit: đơn vị tính (Hộp, Ống, Tuýp, Lọ, Gói, Chiếc, Bộ, Cuộn, Cái)\n"
        "- quantity: số lượng (số nguyên dương, mặc định 1)\n"
        "- min_threshold: mức tồn tối thiểu cần báo động (số nguyên, mặc định 5)\n"
        "- expiry_date: ngày hết hạn định dạng 'YYYY-MM-DD' hoặc null nếu không nói\n"
        "- unit_price: đơn giá nhập bằng VNĐ (số nguyên, VD: 450k -> 450000)\n"
        "- supplier: tên nhà cung cấp nếu có hoặc rỗng\n"
        "- notes: ghi chú thêm nếu có\n"
        "Chỉ trả về JSON thuần túy, không có markdown formatting."
    )
    prompt = f"Phân tích thông tin vật tư từ đoạn văn bản sau:\n\"{text.strip()}\""

    try:
        raw_res = call_gemini_api(prompt, system_instruction=system_instruction, api_key=api_key, json_mode=True)
        if raw_res:
            cleaned = raw_res.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned)
            parsed = json.loads(cleaned)
            result = {
                "name": str(parsed.get("name") or fallback["name"]).strip(),
                "category": str(parsed.get("category") or fallback["category"]).strip(),
                "unit": str(parsed.get("unit") or fallback["unit"]).strip(),
                "quantity": int(parsed.get("quantity") or fallback["quantity"]),
                "min_threshold": int(parsed.get("min_threshold") or fallback["min_threshold"]),
                "expiry_date": parsed.get("expiry_date") or fallback["expiry_date"],
                "unit_price": int(parsed.get("unit_price") or fallback["unit_price"]),
                "supplier": str(parsed.get("supplier") or fallback["supplier"]).strip(),
                "notes": str(parsed.get("notes") or fallback["notes"]).strip(),
                "ai_source": "gemini"
            }
            return result
    except Exception:
        pass

    fallback["ai_source"] = "rule_based"
    return fallback


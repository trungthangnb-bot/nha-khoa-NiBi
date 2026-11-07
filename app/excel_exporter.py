"""
Nha Khoa NiBi - BS. Thắng
Mô-đun xuất file Excel (.xlsx) chuẩn mẫu 'Quản Lý PK NiBi 2026'
Bao gồm các sheet: Dữ liệu nhập, CÒN NỢ, Hẹn BN, Răng giả, Danh Sách BN, Doanh Thu
"""
import io
import datetime as dt
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .db import db

def create_styled_cell(ws, row, col, value, font=None, fill=None, alignment=None, border=None, number_format=None):
    cell = ws.cell(row=row, column=col, value=value)
    if font: cell.font = font
    if fill: cell.fill = fill
    if alignment: cell.alignment = alignment
    if border: cell.border = border
    if number_format: cell.number_format = number_format
    return cell

def auto_fit_columns(ws, min_width=10, max_width=50):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val = str(cell.value or '')
            if '\n' in val:
                val = max(val.split('\n'), key=len)
            max_len = max(max_len, len(val))
        ws.column_dimensions[col_letter].width = max(min(max_len + 3, max_width), min_width)

def safe_k(val):
    if not val:
        return 0.0
    try:
        return round(float(val) / 1000, 1)
    except (ValueError, TypeError):
        return 0.0

def safe_age(current_year, yob):
    if not yob:
        return ""
    try:
        y = int(str(yob).strip())
        return current_year - y
    except (ValueError, TypeError):
        return ""

def generate_nibi_excel_workbook() -> io.BytesIO:
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # Styles
    font_header = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    font_bold = Font(name="Segoe UI", size=10, bold=True, color="0F172A")
    font_regular = Font(name="Segoe UI", size=10, color="1E293B")
    font_title = Font(name="Segoe UI", size=14, bold=True, color="0F766E")
    font_sub = Font(name="Segoe UI", size=10, italic=True, color="64748B")

    fill_teal = PatternFill(start_color="0D9488", end_color="0D9488", fill_type="solid")
    fill_rose = PatternFill(start_color="BE123C", end_color="BE123C", fill_type="solid")
    fill_amber = PatternFill(start_color="D97706", end_color="D97706", fill_type="solid")
    fill_blue = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    fill_summary = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_all = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    current_year = dt.date.today().year

    with db() as conn:
        conn.row_factory = None

        # =========================================================================
        # 1. SHEET: Dữ liệu nhập (Chuẩn mẫu gốc Quản lý PK NiBi 2026)
        # =========================================================================
        ws1 = wb.create_sheet(title="Dữ liệu nhập")
        ws1.views.sheetView[0].showGridLines = True
        ws1.freeze_panes = "A2"

        headers1 = [
            "PK", "ngày", "Họ Tên", "Năm Sinh", "Tuổi", "SĐT", "địa chỉ",
            "Khách hàng", "Nha sỹ", "Điều Trị", "TỔNG TIỀN (K)", "Thanh Toán",
            "Đã Thanh Toán (K)", "CÒN NỢ (K)", "Trạng Thái", "Ngày Hẹn Đtrị", "Ghi chú"
        ]

        for col_idx, h in enumerate(headers1, 1):
            create_styled_cell(ws1, 1, col_idx, h, font=font_header, fill=fill_teal, alignment=align_center, border=border_all)
        ws1.row_dimensions[1].height = 26

        rows1 = conn.execute("""
            SELECT 
                COALESCE(t.branch, p.branch) as pk,
                COALESCE(t.visit_date, substr(p.created_at, 1, 10)) as ngay,
                p.name as ho_ten,
                p.yob as nam_sinh,
                p.phone as sdt,
                p.address as dia_chi,
                t.category as hang_muc,
                COALESCE(t.dentist, 'Thắng') as nha_sy,
                t.procedure as dieu_tri,
                t.cost as tong_tien,
                (SELECT method FROM payments WHERE treatment_id = t.id ORDER BY id DESC LIMIT 1) as thanh_toan,
                (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE treatment_id = t.id) as da_thu,
                (SELECT appt_date FROM appointments WHERE patient_id = p.id AND appt_date >= t.visit_date ORDER BY appt_date ASC LIMIT 1) as hen_dtri,
                t.notes as ghi_chu
            FROM treatments t
            JOIN patients p ON p.id = t.patient_id
            ORDER BY t.visit_date DESC, t.id DESC
        """).fetchall()

        total_cost_k = 0
        total_paid_k = 0
        total_debt_k = 0

        for r_idx, r in enumerate(rows1, 2):
            pk, ngay, ho_ten, nam_sinh, sdt, dia_chi, hang_muc, nha_sy, dieu_tri, cost, thanh_toan, da_thu, hen_dtri, ghi_chu = r
            
            cost_k = safe_k(cost)
            paid_k = safe_k(da_thu)
            debt_k = max(round(cost_k - paid_k, 1), 0)
            tuoi = safe_age(current_year, nam_sinh)

            if debt_k == 0:
                trang_thai = "Đã xong"
            elif hen_dtri:
                trang_thai = "Hẹn D.trị"
            else:
                trang_thai = "Còn nợ"

            total_cost_k += cost_k
            total_paid_k += paid_k
            total_debt_k += debt_k

            row_data = [
                (pk, align_center, None),
                (ngay, align_center, None),
                (ho_ten, align_left, None),
                (nam_sinh if nam_sinh else "", align_center, None),
                (tuoi, align_center, None),
                (sdt or "", align_center, None),
                (dia_chi or "", align_left, None),
                (hang_muc or "", align_left, None),
                (nha_sy or "", align_center, None),
                (dieu_tri or "", align_left, None),
                (cost_k, align_right, "#,##0.0"),
                (thanh_toan or "Tiền mặt", align_center, None),
                (paid_k, align_right, "#,##0.0"),
                (debt_k, align_right, "#,##0.0"),
                (trang_thai, align_center, None),
                (hen_dtri or "", align_center, None),
                (ghi_chu or "", align_left, None)
            ]

            for c_idx, (val, aln, num_f) in enumerate(row_data, 1):
                fnt = font_bold if c_idx == 3 else font_regular
                create_styled_cell(ws1, r_idx, c_idx, val, font=fnt, alignment=aln, border=border_all, number_format=num_f)
            ws1.row_dimensions[r_idx].height = 20

        # Summary Row at Bottom
        last_row = len(rows1) + 2
        create_styled_cell(ws1, last_row, 3, "TỔNG CỘNG TOÀN BỘ:", font=font_bold, fill=fill_summary, alignment=align_left, border=border_all)
        create_styled_cell(ws1, last_row, 11, total_cost_k, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")
        create_styled_cell(ws1, last_row, 13, total_paid_k, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")
        create_styled_cell(ws1, last_row, 14, total_debt_k, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")

        auto_fit_columns(ws1)

        # =========================================================================
        # 2. SHEET: CÒN NỢ (Danh sách bệnh nhân đang nợ tiền)
        # =========================================================================
        ws2 = wb.create_sheet(title="CÒN NỢ")
        ws2.views.sheetView[0].showGridLines = True
        ws2.freeze_panes = "A2"

        headers2 = ["STT", "Mã BN", "Họ Tên", "Năm Sinh", "SĐT", "Địa Chỉ", "Cơ Sở", "Nợ Từ Ngày", "Các Ca Điều Trị Còn Nợ", "SỐ TIỀN CÒN NỢ (K)"]
        for col_idx, h in enumerate(headers2, 1):
            create_styled_cell(ws2, 1, col_idx, h, font=font_header, fill=fill_rose, alignment=align_center, border=border_all)
        ws2.row_dimensions[1].height = 26

        rows2 = conn.execute("""
            WITH tp AS (
                SELECT t.id, t.patient_id, t.procedure, t.cost, t.visit_date,
                       COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id = t.id), 0) as paid
                FROM treatments t
            )
            SELECT 
                p.id, p.code, p.name, p.yob, p.phone, p.address, p.branch,
                MIN(tp.visit_date) as oldest_debt_date,
                SUM(MAX(tp.cost - tp.paid, 0)) as total_debt,
                GROUP_CONCAT(tp.procedure || ' (nợ ' || CAST((tp.cost - tp.paid)/1000 AS INT) || 'K)', '; ') as details
            FROM patients p
            JOIN tp ON tp.patient_id = p.id
            WHERE tp.cost > tp.paid
            GROUP BY p.id
            ORDER BY total_debt DESC
        """).fetchall()

        sum_debt_k = 0
        for r_idx, r in enumerate(rows2, 2):
            pid, code, name, yob, phone, address, branch, oldest, debt, details = r
            debt_k = safe_k(debt)
            sum_debt_k += debt_k

            row_data = [
                (r_idx - 1, align_center, None),
                (code or f"BN{pid:05d}", align_center, None),
                (name, align_left, None),
                (yob or "", align_center, None),
                (phone or "", align_center, None),
                (address or "", align_left, None),
                (branch or "", align_center, None),
                (oldest or "", align_center, None),
                (details or "", align_left, None),
                (debt_k, align_right, "#,##0.0")
            ]
            for c_idx, (val, aln, num_f) in enumerate(row_data, 1):
                fnt = font_bold if c_idx in (3, 10) else font_regular
                create_styled_cell(ws2, r_idx, c_idx, val, font=fnt, alignment=aln, border=border_all, number_format=num_f)
            ws2.row_dimensions[r_idx].height = 20

        last_row2 = len(rows2) + 2
        create_styled_cell(ws2, last_row2, 3, f"TỔNG CỘNG CÔNG NỢ ({len(rows2)} bệnh nhân):", font=font_bold, fill=fill_summary, alignment=align_left, border=border_all)
        create_styled_cell(ws2, last_row2, 10, sum_debt_k, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")

        auto_fit_columns(ws2)

        # =========================================================================
        # 3. SHEET: Hẹn BN (Lịch hẹn khám & Tái khám định kỳ)
        # =========================================================================
        ws3 = wb.create_sheet(title="Hẹn BN")
        ws3.views.sheetView[0].showGridLines = True
        ws3.freeze_panes = "A2"

        headers3 = ["STT", "Ngày Hẹn", "Giờ Hẹn", "Cơ Sở", "Mã BN", "Họ Tên", "SĐT", "Địa Chỉ", "Nội Dung Lịch Hẹn", "Loại Hẹn", "Trạng Thái"]
        for col_idx, h in enumerate(headers3, 1):
            create_styled_cell(ws3, 1, col_idx, h, font=font_header, fill=fill_blue, alignment=align_center, border=border_all)
        ws3.row_dimensions[1].height = 26

        rows3 = conn.execute("""
            SELECT 
                a.appt_date, a.appt_time, COALESCE(a.branch, p.branch) as branch,
                p.code, p.name, p.phone, p.address, a.reason,
                CASE WHEN a.kind = 'tai_kham' THEN 'Tái khám' ELSE 'Hẹn khám mới' END as loai_hen,
                a.status
            FROM appointments a
            JOIN patients p ON p.id = a.patient_id
            ORDER BY a.appt_date ASC, a.appt_time ASC
        """).fetchall()

        for r_idx, r in enumerate(rows3, 2):
            appt_date, appt_time, branch, code, name, phone, address, reason, loai_hen, status = r
            row_data = [
                (r_idx - 1, align_center),
                (appt_date, align_center),
                (appt_time or "", align_center),
                (branch or "", align_center),
                (code or "", align_center),
                (name, align_left),
                (phone or "", align_center),
                (address or "", align_left),
                (reason or "", align_left),
                (loai_hen, align_center),
                (status or "Chờ đến", align_center)
            ]
            for c_idx, (val, aln) in enumerate(row_data, 1):
                fnt = font_bold if c_idx == 6 else font_regular
                create_styled_cell(ws3, r_idx, c_idx, val, font=fnt, alignment=aln, border=border_all)
            ws3.row_dimensions[r_idx].height = 20

        auto_fit_columns(ws3)

        # =========================================================================
        # 4. SHEET: Răng giả & Chỉnh nha
        # =========================================================================
        ws4 = wb.create_sheet(title="Răng giả - Chỉnh nha")
        ws4.views.sheetView[0].showGridLines = True
        ws4.freeze_panes = "A2"

        headers4 = ["STT", "Hạng Mục", "Ngày Bắt Đầu", "Cơ Sở", "Mã BN", "Họ Tên", "Năm Sinh", "SĐT", "Địa Chỉ", "Nha Sỹ", "Thủ Thuật Điều Trị", "Chi Phí (K)", "Lịch Hẹn Tái Khám Kế Tiếp"]
        for col_idx, h in enumerate(headers4, 1):
            create_styled_cell(ws4, 1, col_idx, h, font=font_header, fill=fill_amber, alignment=align_center, border=border_all)
        ws4.row_dimensions[1].height = 26

        rows4 = conn.execute("""
            SELECT 
                t.category, t.visit_date, COALESCE(t.branch, p.branch),
                p.code, p.name, p.yob, p.phone, p.address, COALESCE(t.dentist, 'Thắng'),
                t.procedure, t.cost,
                (SELECT appt_date FROM appointments WHERE patient_id = p.id AND appt_date >= t.visit_date ORDER BY appt_date ASC LIMIT 1) as next_appt
            FROM treatments t
            JOIN patients p ON p.id = t.patient_id
            WHERE t.category IN ('Răng giả', 'Chỉnh nha')
            ORDER BY t.category ASC, t.visit_date DESC
        """).fetchall()

        for r_idx, r in enumerate(rows4, 2):
            category, visit_date, branch, code, name, yob, phone, address, dentist, procedure, cost, next_appt = r
            cost_k = safe_k(cost)

            row_data = [
                (r_idx - 1, align_center, None),
                (category, align_center, None),
                (visit_date or "", align_center, None),
                (branch or "", align_center, None),
                (code or "", align_center, None),
                (name, align_left, None),
                (yob or "", align_center, None),
                (phone or "", align_center, None),
                (address or "", align_left, None),
                (dentist or "", align_center, None),
                (procedure or "", align_left, None),
                (cost_k, align_right, "#,##0.0"),
                (next_appt or "", align_center, None)
            ]
            for c_idx, (val, aln, num_f) in enumerate(row_data, 1):
                fnt = font_bold if c_idx == 6 else font_regular
                create_styled_cell(ws4, r_idx, c_idx, val, font=fnt, alignment=aln, border=border_all, number_format=num_f)
            ws4.row_dimensions[r_idx].height = 20

        auto_fit_columns(ws4)

        # =========================================================================
        # 5. SHEET: Danh Sách Bệnh Nhân (Toàn bộ 305+ bệnh nhân)
        # =========================================================================
        ws5 = wb.create_sheet(title="Danh Sách Bệnh Nhân")
        ws5.views.sheetView[0].showGridLines = True
        ws5.freeze_panes = "A2"

        headers5 = ["STT", "Mã BN", "Họ và Tên", "Năm Sinh", "Tuổi", "SĐT", "Ghi Chú SĐT", "Địa Chỉ", "Cơ Sở", "Tiền Sử Bệnh", "Số Ca ĐT", "Tổng Chi Phí (K)", "Đã Trả (K)", "Còn Nợ (K)"]
        for col_idx, h in enumerate(headers5, 1):
            create_styled_cell(ws5, 1, col_idx, h, font=font_header, fill=fill_teal, alignment=align_center, border=border_all)
        ws5.row_dimensions[1].height = 26

        rows5 = conn.execute("""
            WITH tp AS (
                SELECT t.id, t.patient_id, t.cost,
                       COALESCE((SELECT SUM(amount) FROM payments WHERE treatment_id = t.id), 0) as paid
                FROM treatments t
            )
            SELECT 
                p.id, p.code, p.name, p.yob, p.phone, p.phone_note, p.address, p.branch, p.medical_history,
                COUNT(tp.id) as visits,
                COALESCE(SUM(tp.cost), 0) as total_cost,
                COALESCE(SUM(tp.paid), 0) as total_paid,
                COALESCE(SUM(MAX(tp.cost - tp.paid, 0)), 0) as total_debt
            FROM patients p
            LEFT JOIN tp ON tp.patient_id = p.id
            GROUP BY p.id
            ORDER BY p.id ASC
        """).fetchall()

        for r_idx, r in enumerate(rows5, 2):
            pid, code, name, yob, phone, phone_note, address, branch, med_hist, visits, cost, paid, debt = r
            tuoi = safe_age(current_year, yob)
            cost_k = safe_k(cost)
            paid_k = safe_k(paid)
            debt_k = safe_k(debt)

            row_data = [
                (r_idx - 1, align_center, None),
                (code or f"BN{pid:05d}", align_center, None),
                (name, align_left, None),
                (yob or "", align_center, None),
                (tuoi, align_center, None),
                (phone or "", align_center, None),
                (phone_note or "", align_left, None),
                (address or "", align_left, None),
                (branch or "", align_center, None),
                (med_hist or "", align_left, None),
                (visits, align_center, None),
                (cost_k, align_right, "#,##0.0"),
                (paid_k, align_right, "#,##0.0"),
                (debt_k, align_right, "#,##0.0")
            ]
            for c_idx, (val, aln, num_f) in enumerate(row_data, 1):
                fnt = font_bold if c_idx == 3 else font_regular
                create_styled_cell(ws5, r_idx, c_idx, val, font=fnt, alignment=aln, border=border_all, number_format=num_f)
            ws5.row_dimensions[r_idx].height = 20

        auto_fit_columns(ws5)

        # =========================================================================
        # 6. SHEET: Doanh Thu & Tổng Hợp
        # =========================================================================
        ws6 = wb.create_sheet(title="Doanh Thu")
        ws6.views.sheetView[0].showGridLines = True
        ws6.freeze_panes = "A2"

        headers6 = ["Cơ Sở", "Tổng Ca Khám", "Tổng Chi Phí (K)", "Đã Thu Tiền Mặt (K)", "Đã Thu Chuyển Khoản (K)", "TỔNG THỰC THU (K)", "CÒN NỢ (K)"]
        for col_idx, h in enumerate(headers6, 1):
            create_styled_cell(ws6, 1, col_idx, h, font=font_header, fill=fill_teal, alignment=align_center, border=border_all)
        ws6.row_dimensions[1].height = 26

        branches = ["Ninh Vân", "Phố Dâu", "Cửu Yên"]
        total_all_billed = 0
        total_all_cash = 0
        total_all_transfer = 0
        total_all_collected = 0
        total_all_debt = 0

        for b_idx, b in enumerate(branches, 2):
            b_cost = conn.execute("SELECT COALESCE(SUM(cost), 0) FROM treatments WHERE branch = ? OR (branch IS NULL AND patient_id IN (SELECT id FROM patients WHERE branch = ?))", (b, b)).fetchone()[0]
            b_cash = conn.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN treatments t ON t.id = p.treatment_id WHERE (t.branch = ? OR (t.branch IS NULL AND p.patient_id IN (SELECT id FROM patients WHERE branch = ?))) AND p.method LIKE '%Tiền mặt%'", (b, b)).fetchone()[0]
            b_transfer = conn.execute("SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN treatments t ON t.id = p.treatment_id WHERE (t.branch = ? OR (t.branch IS NULL AND p.patient_id IN (SELECT id FROM patients WHERE branch = ?))) AND p.method LIKE '%Chuyển khoản%'", (b, b)).fetchone()[0]
            b_visits = conn.execute("SELECT COUNT(id) FROM treatments WHERE branch = ? OR (branch IS NULL AND patient_id IN (SELECT id FROM patients WHERE branch = ?))", (b, b)).fetchone()[0]

            cost_k = safe_k(b_cost)
            cash_k = safe_k(b_cash)
            transfer_k = safe_k(b_transfer)
            collected_k = round(cash_k + transfer_k, 1)
            debt_k = max(round(cost_k - collected_k, 1), 0)

            total_all_billed += cost_k
            total_all_cash += cash_k
            total_all_transfer += transfer_k
            total_all_collected += collected_k
            total_all_debt += debt_k

            row_data = [
                (b, align_center, None),
                (b_visits, align_center, None),
                (cost_k, align_right, "#,##0.0"),
                (cash_k, align_right, "#,##0.0"),
                (transfer_k, align_right, "#,##0.0"),
                (collected_k, align_right, "#,##0.0"),
                (debt_k, align_right, "#,##0.0")
            ]
            for c_idx, (val, aln, num_f) in enumerate(row_data, 1):
                fnt = font_bold if c_idx == 1 else font_regular
                create_styled_cell(ws6, b_idx, c_idx, val, font=fnt, alignment=aln, border=border_all, number_format=num_f)
            ws6.row_dimensions[b_idx].height = 22

        # Total row
        last_b_row = len(branches) + 2
        create_styled_cell(ws6, last_b_row, 1, "TỔNG CỘNG 3 CƠ SỞ:", font=font_bold, fill=fill_summary, alignment=align_center, border=border_all)
        create_styled_cell(ws6, last_b_row, 3, total_all_billed, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")
        create_styled_cell(ws6, last_b_row, 4, total_all_cash, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")
        create_styled_cell(ws6, last_b_row, 5, total_all_transfer, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")
        create_styled_cell(ws6, last_b_row, 6, total_all_collected, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")
        create_styled_cell(ws6, last_b_row, 7, total_all_debt, font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")

        auto_fit_columns(ws6)

        # =========================================================================
        # 7. SHEET: Vật Tư & Tồn Kho (Kho Thuốc, Cảnh Báo Date & Tồn Kho)
        # =========================================================================
        ws7 = wb.create_sheet(title="Vật Tư & Tồn Kho")
        ws7.views.sheetView[0].showGridLines = True
        ws7.freeze_panes = "A2"

        fill_date_red = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
        fill_date_yellow = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
        fill_date_green = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
        fill_low_stock = PatternFill(start_color="FFEDD5", end_color="FFEDD5", fill_type="solid")

        headers7 = [
            "Mã VT", "Tên Vật Tư / Thuốc", "Nhóm Danh Mục", "ĐVT",
            "Số Lượng Tồn", "Tồn Tối Thiểu", "Hạn Dùng (HSD)", "Tình Trạng HSD",
            "Đơn Giá (K)", "Thành Tiền (K)", "Nhà Cung Cấp", "Cơ Sở", "Ghi Chú"
        ]
        for col_idx, h in enumerate(headers7, 1):
            create_styled_cell(ws7, 1, col_idx, h, font=font_header, fill=fill_teal, alignment=align_center, border=border_all)
        ws7.row_dimensions[1].height = 26

        sup_rows = conn.execute("SELECT * FROM supplies ORDER BY CASE WHEN quantity <= min_threshold THEN 0 ELSE 1 END, id DESC").fetchall()
        today = dt.date.today()
        total_sup_val = 0

        for r_idx, s in enumerate(sup_rows, 2):
            s_dict = dict(s)
            qty = int(s_dict.get("quantity") or 0)
            min_th = int(s_dict.get("min_threshold") or 0)
            price_k = safe_k(s_dict.get("unit_price") or 0)
            val_k = round(qty * price_k, 1)
            total_sup_val += val_k

            exp_str = s_dict.get("expiry_date") or ""
            date_status_txt = "Không có HSD"
            status_fill = None

            if exp_str:
                try:
                    exp_d = dt.date.fromisoformat(str(exp_str)[:10])
                    days_left = (exp_d - today).days
                    if days_left < 0:
                        date_status_txt = f"🔴 ĐÃ HẾT HẠN ({abs(days_left)} ngày)"
                        status_fill = fill_date_red
                    elif days_left <= 30:
                        date_status_txt = f"🔴 CẬN DATE ({days_left} ngày)"
                        status_fill = fill_date_red
                    elif days_left <= 180:
                        months_left = max(1, days_left // 30)
                        date_status_txt = f"🟡 CẢNH BÁO (~{months_left} tháng)"
                        status_fill = fill_date_yellow
                    elif days_left > 365:
                        date_status_txt = "🟢 AN TOÀN (> 1 năm)"
                        status_fill = fill_date_green
                    else:
                        date_status_txt = "Bình thường (6-12 tháng)"
                except Exception:
                    date_status_txt = exp_str

            is_low = qty <= min_th
            qty_fill = fill_low_stock if is_low else None

            row_data = [
                (s_dict.get("code") or f"VT{s_dict.get('id', 0):05d}", align_center, None, font_bold, None),
                (s_dict.get("name") or "", align_left, None, font_bold, None),
                (s_dict.get("category") or "", align_center, None, font_regular, None),
                (s_dict.get("unit") or "", align_center, None, font_regular, None),
                (qty, align_center, None, font_bold if is_low else font_regular, qty_fill),
                (min_th, align_center, None, font_regular, None),
                (exp_str, align_center, None, font_regular, None),
                (date_status_txt, align_center, None, font_bold if status_fill else font_regular, status_fill),
                (price_k, align_right, "#,##0.0", font_regular, None),
                (val_k, align_right, "#,##0.0", font_bold, None),
                (s_dict.get("supplier") or "", align_left, None, font_regular, None),
                (s_dict.get("branch") or "Ninh Vân", align_center, None, font_regular, None),
                (s_dict.get("notes") or "", align_left, None, font_regular, None),
            ]

            for c_idx, (val, aln, num_f, fnt, f_cell) in enumerate(row_data, 1):
                create_styled_cell(ws7, r_idx, c_idx, val, font=fnt, fill=f_cell, alignment=aln, border=border_all, number_format=num_f)
            ws7.row_dimensions[r_idx].height = 22

        last_s_row = len(sup_rows) + 2
        create_styled_cell(ws7, last_s_row, 1, "TỔNG GIÁ TRỊ TỒN KHO:", font=font_bold, fill=fill_summary, alignment=align_center, border=border_all)
        create_styled_cell(ws7, last_s_row, 10, round(total_sup_val, 1), font=font_bold, fill=fill_summary, alignment=align_right, border=border_all, number_format="#,##0.0")

        auto_fit_columns(ws7)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

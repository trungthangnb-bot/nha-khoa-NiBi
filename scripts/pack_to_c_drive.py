import os
import shutil
import zipfile
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = r"C:\Users\Thang Pham\.gemini\antigravity\scratch\nibi-dental"
TARGET_DIR = r"C:\QL NK NiBi"

os.makedirs(TARGET_DIR, exist_ok=True)
print(f"Bắt đầu gom dữ liệu quan trọng vào: {TARGET_DIR}")

# 1. Copy Database file gốc
data_dir = os.path.join(TARGET_DIR, "data")
os.makedirs(data_dir, exist_ok=True)
db_src = os.path.join(BASE_DIR, "data", "nibi.db")
if os.path.exists(db_src):
    shutil.copy2(db_src, os.path.join(data_dir, "nibi.db"))
    shutil.copy2(db_src, os.path.join(TARGET_DIR, "nibi_goc.db"))
    print("✅ Đã sao chép file Database SQLite gốc (nibi.db và nibi_goc.db)")

# 2. Copy config và link truy cập
for fname in ["turso_config.json", "LINK_TRUY_CAP_DIEN_THOAI.txt", "qr_code_dienthoai.png", "requirements.txt", "render.yaml", "start_remote.py"]:
    fpath = os.path.join(BASE_DIR, fname)
    if os.path.exists(fpath):
        shutil.copy2(fpath, os.path.join(TARGET_DIR, fname))
        print(f"✅ Đã sao chép {fname}")

# 3. Copy thư mục app, static, scripts
for folder in ["app", "static", "scripts"]:
    src_f = os.path.join(BASE_DIR, folder)
    dest_f = os.path.join(TARGET_DIR, folder)
    if os.path.exists(dest_f):
        shutil.rmtree(dest_f)
    shutil.copytree(src_f, dest_f)
    print(f"✅ Đã sao chép toàn bộ thư mục: {folder}/")

# 4. Copy cloudflared.exe nếu có
cf_exe = os.path.join(BASE_DIR, "cloudflared.exe")
if os.path.exists(cf_exe):
    shutil.copy2(cf_exe, os.path.join(TARGET_DIR, "cloudflared.exe"))
    print("✅ Đã sao chép cloudflared.exe")

# 5. Tạo file Bat: Đồng bộ dữ liệu mới nhất từ Cloud về máy
sync_bat = os.path.join(TARGET_DIR, "Dong_Bo_Du_Lieu_Ve_May.bat")
with open(sync_bat, "w", encoding="utf-8") as f:
    f.write(f"""@echo off
title Dong Bo Du Lieu Nha Khoa NiBi
cd /d "{TARGET_DIR}"
"{sys.executable}" scripts\\sync_turso_to_local.py
pause
""")
print("✅ Đã tạo file Dong_Bo_Du_Lieu_Ve_May.bat")

# 6. Tạo file Bat: Khởi động phần mềm trên máy tính
start_bat = os.path.join(TARGET_DIR, "Khoi_Dong_Nha_Khoa_NiBi.bat")
with open(start_bat, "w", encoding="utf-8") as f:
    f.write(f"""@echo off
title Nha Khoa NiBi - Khoi Dong May Chu
cd /d "{TARGET_DIR}"
"{sys.executable}" start_remote.py
pause
""")
print("✅ Đã tạo file Khoi_Dong_Nha_Khoa_NiBi.bat")

# 7. Tạo file Text Hướng dẫn chi tiết
readme_txt = os.path.join(TARGET_DIR, "THONG_TIN_VA_HUONG_DAN.txt")
with open(readme_txt, "w", encoding="utf-8") as f:
    f.write("""================================================================================
🦷 NHA KHOA NIBI - BS. THẮNG | THƯ MỤC LƯU TRỮ DỮ LIỆU GỐC & MÃ NGUỒN PHÒNG KHÁM
================================================================================

Thư mục: C:\\QL NK NiBi

1. CÁC FILE DỮ LIỆU QUAN TRỌNG NHẤT:
   - nibi_goc.db (hoặc data/nibi.db): Cơ sở dữ liệu SQLite gốc chứa toàn bộ hơn 300 bệnh nhân,
     hồ sơ điều trị, các đợt khám, lịch tái khám, công nợ và thanh toán.
   - Quan_Ly_PK_NiBi_2026.xlsx: File Excel tổng hợp 6 sheet mới nhất (Bệnh nhân, Điều trị,
     Thu nợ, Lịch hẹn, Tổng hợp doanh thu) có thể mở bằng Excel bất cứ lúc nào.
   - turso_config.json: Cấu hình kết nối Cloud Database trực tuyến Singapore.

2. CÁCH TRUY CẬP VÀ SỬ DỤNG:
   - Trên điện thoại (4G/5G): https://nha-khoa-nibi.onrender.com
   - Trên máy tính phòng khám: http://localhost:8000
   - Mật khẩu Quản trị (Admin): 123456
   - Mã PIN nhân viên sửa đổi: 0000

3. CÁC TÍNH NĂNG NHANH:
   - Muốn mở phần mềm trên máy tính: Bấm đúp file 'Khoi_Dong_Nha_Khoa_NiBi.bat'
   - Muốn tải toàn bộ dữ liệu mới nhất từ Đám mây về máy: Bấm đúp file 'Dong_Bo_Du_Lieu_Ve_May.bat'
   - File nén sao lưu toàn bộ: 'Sao_Luu_Toan_Bo_NhaKhoaNiBi.zip'

================================================================================
""")
print("✅ Đã tạo file THONG_TIN_VA_HUONG_DAN.txt")

# 8. Tạo file Zip nén toàn bộ thư mục để anh lưu trữ hoặc chép vào USB
zip_path = os.path.join(TARGET_DIR, "Sao_Luu_Toan_Bo_NhaKhoaNiBi.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(TARGET_DIR):
        for file in files:
            if file == "Sao_Luu_Toan_Bo_NhaKhoaNiBi.zip":
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, TARGET_DIR)
            z.write(full_path, rel_path)
print(f"✅ Đã tạo file nén sao lưu tổng thể: {zip_path} ({os.path.getsize(zip_path)} bytes)")

print("\n🎉 HOÀN TẤT GOM DỮ LIỆU VÀO C:\\QL NK NiBi!")

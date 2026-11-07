import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import urllib.request
import json
import time

RENDER_BASE = "https://nha-khoa-nibi.onrender.com"
LOCAL_BASE = "http://127.0.0.1:8000"

print("1. Đăng nhập Admin trên Render...")
req = urllib.request.Request(
    f"{RENDER_BASE}/api/auth/login",
    data=json.dumps({"username": "admin", "password": "123456"}).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req, timeout=15) as resp:
    token_render = json.loads(resp.read().decode("utf-8"))["token"]
print("   -> Đăng nhập Render thành công!")

print("\n2. Thử tạo 1 bệnh nhân mới trên Render (mô phỏng nhập qua 4G trên điện thoại)...")
req_create = urllib.request.Request(
    f"{RENDER_BASE}/api/patients",
    data=json.dumps({
        "name": "Bệnh Nhân Test Đồng Bộ 4G",
        "phone": "0988777666",
        "yob": 1998,
        "address": "Ninh Vân",
        "branch": "Ninh Vân"
    }).encode("utf-8"),
    headers={"Authorization": f"Bearer {token_render}", "Content-Type": "application/json"}
)
with urllib.request.urlopen(req_create, timeout=15) as resp:
    created = json.loads(resp.read().decode("utf-8"))
    test_id = created["id"]
    test_code = created["code"]
print(f"   -> Đã tạo bệnh nhân trên Render: {test_code} (ID: {test_id})")

print("\n3. Kiểm tra ngay trên máy tính phòng khám (Localhost:8000)...")
req_local = urllib.request.Request(
    f"{LOCAL_BASE}/api/auth/login",
    data=json.dumps({"username": "admin", "password": "123456"}).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req_local, timeout=15) as resp:
    token_local = json.loads(resp.read().decode("utf-8"))["token"]

req_check = urllib.request.Request(
    f"{LOCAL_BASE}/api/patients/{test_id}",
    headers={"Authorization": f"Bearer {token_local}"}
)
with urllib.request.urlopen(req_check, timeout=15) as resp:
    found = json.loads(resp.read().decode("utf-8"))
    print(f"   -> THÀNH CÔNG! Máy tính phòng khám đã thấy ngay: {found['patient']['code']} - {found['patient']['name']}")

print("\n4. Dọn dẹp: Xóa bệnh nhân test trên Render...")
req_del = urllib.request.Request(
    f"{RENDER_BASE}/api/patients/{test_id}/delete",
    data=json.dumps({"password": "123456"}).encode("utf-8"),
    headers={"Authorization": f"Bearer {token_render}", "Content-Type": "application/json"}
)
with urllib.request.urlopen(req_del, timeout=15) as resp:
    del_res = json.loads(resp.read().decode("utf-8"))
    print("   -> Đã xóa bệnh nhân test sạch sẽ, kết quả:", del_res)

print("\n🎉 KẾT QUẢ ĐỐI SOÁT: TOÀN BỘ HỆ THỐNG ĐÃ HOẠT ĐỘNG HOÀN HẢO 100%!")

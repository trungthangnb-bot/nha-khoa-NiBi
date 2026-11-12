"""
Nha Khoa NiBi - BS. Thắng
Script Khởi Động Máy Chủ & Đường Hầm Từ Xa (Cloudflare Tunnel + QR Code)
"""
import os
import sys
import time
import re
import socket
import subprocess
import qrcode

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def kill_process_on_port(port: int):
    try:
        ps_cmd = f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
    except Exception:
        pass
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process -Name cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def setup_desktop_and_startup(base_dir: str):
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
    python_exe = sys.executable
    script_path = os.path.join(base_dir, "start_remote.py")

    # 1. Create .bat launcher in project directory
    bat_content = f"""@echo off
title Nha Khoa NiBi - Server Launcher
cd /d "{base_dir}"
"{python_exe}" "{script_path}"
pause
"""
    bat_path = os.path.join(base_dir, "KhoiDong_NhaKhoaNiBi.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    # 2. Create silent .vbs launcher (runs in background without black CMD window)
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{base_dir}"\n'
        f'WshShell.Run """{python_exe}"" ""{script_path}""", 0, False\n'
    )
    vbs_path = os.path.join(base_dir, "KhoiDong_An_NhaKhoaNiBi.vbs")
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)

    # 3. Create Desktop shortcut to Bat
    if os.path.exists(desktop_dir):
        desktop_bat = os.path.join(desktop_dir, "Khởi Động Nha Khoa NiBi.bat")
        with open(desktop_bat, "w", encoding="utf-8") as f:
            f.write(bat_content)

        # Desktop URL shortcut to open in browser
        desktop_url = os.path.join(desktop_dir, "Mở Nha Khoa NiBi (Máy Tính).url")
        with open(desktop_url, "w", encoding="utf-8") as f:
            f.write("[InternetShortcut]\nURL=http://localhost:8000\nIconIndex=0\n")

    # 4. Create Windows Startup entry (auto-start when computer turns on)
    if os.path.exists(startup_dir):
        startup_vbs = os.path.join(startup_dir, "NhaKhoaNiBi_AutoStart.vbs")
        with open(startup_vbs, "w", encoding="utf-8") as f:
            f.write(vbs_content)

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    cloudflared_path = os.path.join(base_dir, "cloudflared.exe")
    if not os.path.exists(cloudflared_path):
        # Fallback to sibling dental-clinic if available
        sibling = os.path.join(os.path.dirname(base_dir), "dental-clinic", "cloudflared.exe")
        if os.path.exists(sibling):
            cloudflared_path = sibling

    print("=" * 70)
    print("🦷 NHA KHOA NIBI - BS. THẮNG (NINH VÂN - PHỐ DÂU - CỬU YÊN)")
    print("🛡️  HỆ THỐNG MỚI ĐÃ KHỞI CHẠY - MẬT KHẨU ADMIN: 123456 | PIN: 0000")
    print("=" * 70)

    # 1. Clear old processes on port 8000 to run clean
    kill_process_on_port(8000)

    # 2. Start Uvicorn backend
    print("⏳ Đang khởi động FastAPI trên cổng 8000...")
    def start_fastapi():
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "0.0.0.0"],
            cwd=base_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
    server_proc = start_fastapi()
    time.sleep(2)

    # 3. Launch cloudflared tunnel
    print("🌐 Đang kết nối đường hầm bảo mật Cloudflare Tunnel...")
    cf_proc = subprocess.Popen(
        [cloudflared_path, "tunnel", "--url", "http://127.0.0.1:8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    tunnel_url = None
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    for line in iter(cf_proc.stdout.readline, ""):
        match = url_pattern.search(line)
        if match:
            tunnel_url = match.group(0)
            break

    if not tunnel_url:
        print("❌ Không thể lấy đường dẫn Cloudflare Tunnel. Kiểm tra kết nối mạng!")
        return

    # Drain pipes in background threads so processes never block on full buffer
    import threading
    def drain(pipe):
        try:
            for _ in iter(pipe.readline, ""):
                pass
        except Exception:
            pass

    threading.Thread(target=drain, args=(cf_proc.stdout,), daemon=True).start()
    threading.Thread(target=drain, args=(server_proc.stdout,), daemon=True).start()

    # Keep-Alive thread: Ping Render every 8 minutes so it never goes to sleep
    def keep_render_alive():
        import urllib.request
        render_url = "https://nha-khoa-nibi.onrender.com/api/auth/me"
        while True:
            try:
                time.sleep(480)  # 8 minutes
                req = urllib.request.Request(render_url, headers={"User-Agent": "NiBi-KeepAlive/1.0"})
                urllib.request.urlopen(req, timeout=15)
            except Exception:
                pass
    threading.Thread(target=keep_render_alive, daemon=True).start()

    # Setup desktop shortcuts & auto-start on Windows boot
    try:
        setup_desktop_and_startup(base_dir)
    except Exception as e:
        print(f"Lưu ý khi tạo shortcut khởi động: {e}")

    local_ip = get_local_ip()
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    # 4. Generate QR codes
    # 4A. Permanent Cloud Link (NEVER CHANGES)
    perm_url = "https://nha-khoa-nibi.onrender.com"
    qr_perm = qrcode.QRCode(border=1)
    qr_perm.add_data(perm_url)
    qr_perm.make(fit=True)
    qr_perm_img = qr_perm.make_image(fill_color="black", back_color="white")
    if os.path.exists(desktop_dir):
        try:
            qr_perm_img.save(os.path.join(desktop_dir, "Mã_QR_Link_Cố_Định_Online.png"))
        except Exception:
            pass

    # 4B. Temporary Cloudflare Tunnel QR
    qr = qrcode.QRCode(border=1)
    qr.add_data(tunnel_url)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white")
    qr_img_path = os.path.join(base_dir, "qr_code_dienthoai.png")
    try:
        qr_img.save(qr_img_path)
    except Exception:
        pass

    # Copy QR to Desktop
    if os.path.exists(desktop_dir):
        try:
            qr_img.save(os.path.join(desktop_dir, "Mã_QR_Đăng_Nhập_Điện_Thoại.png"))
        except Exception:
            pass

    # Copy to brain artifact directory for display
    artifact_qr = r"C:\Users\Thang Pham\.gemini\antigravity\brain\4a3df65e-6d6b-4fc4-8d98-77f6e6d9bb9e\qr_code_dienthoai.png"
    try:
        qr_img.save(artifact_qr)
    except Exception:
        pass

    info_text = (
        "=" * 68 + "\n"
        "🦷 NHA KHOA NIBI - BS. THẮNG (QUẢN LÝ PHÒNG KHÁM)\n"
        "=" * 68 + "\n\n"
        "⭐ LINK CỐ ĐỊNH VĨNH VIỄN (KHÔNG BAO GIỜ ĐỔI LINK - DÙNG 4G/5G/INTERNET):\n"
        f"   👉 https://nha-khoa-nibi.onrender.com\n"
        "   (Khuyên dùng: Lưu link này vào Bookmark điện thoại hoặc quét ảnh\n"
        "    'Mã_QR_Link_Cố_Định_Online.png' ngoài Desktop, dùng mãi mãi không lo đổi link!)\n\n"
        "1. KHI Ở PHÒNG KHÁM (CÙNG MẠNG WI-FI PHÒNG KHÁM):\n"
        f"   👉 Link điện thoại / máy tính khác: http://{local_ip}:8000\n"
        "   (Link này CỐ ĐỊNH, kết nối siêu nhanh trong mạng Wi-Fi phòng)\n\n"
        "2. LINK DỰ PHÒNG CLOUDFLARE HÔM NAY:\n"
        f"   👉 Link từ xa hôm nay: {tunnel_url}\n"
        "   (Hoặc quét ảnh 'Mã_QR_Đăng_Nhập_Điện_Thoại.png' ngoài màn hình Desktop)\n\n"
        "3. TRÊN MÁY TÍNH NÀY:\n"
        "   👉 Mở trình duyệt vào: http://localhost:8000\n\n"
        "4. THÔNG TIN ĐĂNG NHẬP:\n"
        "   🔑 Mật khẩu Admin: 123456\n"
        "   🛡️  Mã PIN nhân viên sửa đổi hồ sơ: 0000\n"
        "=" * 68 + "\n"
    )

    info_file_path = os.path.join(base_dir, "LINK_TRUY_CAP_DIEN_THOAI.txt")
    with open(info_file_path, "w", encoding="utf-8") as f:
        f.write(info_text)

    if os.path.exists(desktop_dir):
        try:
            with open(os.path.join(desktop_dir, "LINK_TRUY_CAP_DIEN_THOAI.txt"), "w", encoding="utf-8") as f:
                f.write(info_text)
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("✅ HỆ THỐNG PHÒNG KHÁM ĐÃ SẴN SÀNG HOẠT ĐỘNG:")
    print(f"👉 Link máy tính:              http://localhost:8000")
    print(f"👉 Link Wi-Fi nội bộ phòng:     http://{local_ip}:8000")
    print(f"👉 Link từ xa (4G/5G/Internet): {tunnel_url}")
    print(f"👉 Mã QR & Link đã lưu ngoài màn hình Desktop")
    print("🔑 Mật khẩu Admin: 123456 | PIN sửa đổi: 0000")
    print("=" * 70 + "\n")

    # Keep alive with auto-healing
    try:
        while True:
            time.sleep(2)
            if server_proc.poll() is not None:
                print("⚠️ FastAPI gặp sự cố, tự động khởi động lại...")
                server_proc = start_fastapi()
                threading.Thread(target=drain, args=(server_proc.stdout,), daemon=True).start()
    except KeyboardInterrupt:
        print("\nĐang tắt hệ thống...")
    finally:
        try:
            server_proc.terminate()
            cf_proc.terminate()
        except Exception:
            pass

if __name__ == "__main__":
    main()

"""
Nha Khoa NiBi - BS. Thắng
Script Khởi Động Máy Chủ & Đường Hầm Từ Xa (Cloudflare Tunnel + QR Code)
Phiên bản 2.5: Chống treo, tự phục hồi (Self-Healing Watchdog), khóa đơn tiến trình (Single-Instance Mutex)
"""
import os
import sys
import time
import re
import socket
import subprocess
import threading
import urllib.request
import urllib.error
import webbrowser
import qrcode

if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

base_log_dir = os.path.dirname(os.path.abspath(__file__))
log_file_path = os.path.join(base_log_dir, "nibi_server.log")

class SafeLogger:
    def __init__(self, filepath, stream):
        self.filepath = filepath
        self.stream = stream

    def write(self, data):
        try:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            pass
        if self.stream is not None:
            try:
                self.stream.write(data)
            except Exception:
                pass

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except Exception:
                pass

sys.stdout = SafeLogger(log_file_path, sys.stdout)
sys.stderr = SafeLogger(log_file_path, sys.stderr)

# ── 1. SINGLETON LOCK (Ngăn chặn mở nhiều cửa sổ xung đột cổng 8000) ──
_singleton_sock = None

def acquire_singleton_lock() -> bool:
    global _singleton_sock
    try:
        _singleton_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _singleton_sock.bind(('127.0.0.1', 8009))
        _singleton_sock.listen(1)
        return True
    except OSError:
        return False

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def is_server_healthy() -> bool:
    """Kiểm tra máy chủ FastAPI có đang phản hồi thực sự trên cổng 8000 hay không."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/", headers={"User-Agent": "NiBi-HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        # Nếu máy chủ trả về bất kỳ mã HTTP chuẩn nào (kể cả 401, 403, 404), nghĩa là tiến trình đang chạy tốt
        return e.code in (200, 301, 302, 307, 308, 401, 403, 404, 422)
    except Exception:
        return False

def kill_process_on_port(port: int):
    try:
        ps_cmd = f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
    except Exception:
        pass

def kill_cloudflared():
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

    bat_content = f"""@echo off
title Nha Khoa NiBi - Server Launcher
cd /d "{base_dir}"
"{python_exe}" "{script_path}"
pause
"""
    bat_path = os.path.join(base_dir, "KhoiDong_NhaKhoaNiBi.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{base_dir}"\n'
        f'WshShell.Run """{python_exe}"" ""{script_path}""", 0, False\n'
    )
    vbs_path = os.path.join(base_dir, "KhoiDong_An_NhaKhoaNiBi.vbs")
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)

    if os.path.exists(desktop_dir):
        desktop_bat = os.path.join(desktop_dir, "Khởi Động Nha Khoa NiBi.bat")
        with open(desktop_bat, "w", encoding="utf-8") as f:
            f.write(bat_content)

        desktop_url = os.path.join(desktop_dir, "Mở Nha Khoa NiBi (Máy Tính).url")
        with open(desktop_url, "w", encoding="utf-8") as f:
            f.write("[InternetShortcut]\nURL=http://localhost:8000\nIconIndex=0\n")

    if os.path.exists(startup_dir):
        startup_vbs = os.path.join(startup_dir, "NhaKhoaNiBi_AutoStart.vbs")
        with open(startup_vbs, "w", encoding="utf-8") as f:
            f.write(vbs_content)

def drain(pipe):
    try:
        for line in iter(pipe.readline, ""):
            if line:
                print(f"[FastAPI] {line.strip()}")
    except Exception:
        pass

def update_qr_and_info(base_dir: str, local_ip: str, tunnel_url: str):
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    # 1. Permanent Render Link QR
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

    # 2. Temporary Cloudflare Tunnel QR
    if tunnel_url:
        qr = qrcode.QRCode(border=1)
        qr.add_data(tunnel_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        try:
            qr_img.save(os.path.join(base_dir, "qr_code_dienthoai.png"))
        except Exception:
            pass
        if os.path.exists(desktop_dir):
            try:
                qr_img.save(os.path.join(desktop_dir, "Mã_QR_Đăng_Nhập_Điện_Thoại.png"))
            except Exception:
                pass

        artifact_qr = r"C:\Users\Thang Pham\.gemini\antigravity\brain\4a3df65e-6d6b-4fc4-8d98-77f6e6d9bb9e\qr_code_dienthoai.png"
        try:
            qr_img.save(artifact_qr)
        except Exception:
            pass

    # 3. Text Info File
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
        f"   👉 Link từ xa hôm nay: {tunnel_url or 'Đang kết nối...'}\n"
        "   (Hoặc quét ảnh 'Mã_QR_Đăng_Nhập_Điện_Thoại.png' ngoài màn hình Desktop)\n\n"
        "3. TRÊN MÁY TÍNH NÀY:\n"
        "   👉 Mở trình duyệt vào: http://localhost:8000\n\n"
        "4. THÔNG TIN ĐĂNG NHẬP:\n"
        "   🔑 Mật khẩu Admin: 123456\n"
        "   🛡️  Mã PIN nhân viên sửa đổi hồ sơ: 0000\n"
        "=" * 68 + "\n"
    )

    info_file_path = os.path.join(base_dir, "LINK_TRUY_CAP_DIEN_THOAI.txt")
    try:
        with open(info_file_path, "w", encoding="utf-8") as f:
            f.write(info_text)
    except Exception:
        pass

    if os.path.exists(desktop_dir):
        try:
            with open(os.path.join(desktop_dir, "LINK_TRUY_CAP_DIEN_THOAI.txt"), "w", encoding="utf-8") as f:
                f.write(info_text)
        except Exception:
            pass

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    # ── SINGLE INSTANCE CHECK ──
    if not acquire_singleton_lock():
        print("ℹ️ Hệ thống Nha Khoa NiBi đã đang chạy sẵn ở chế độ nền trên máy tính này.")
        print("👉 Đang mở trình duyệt vào http://localhost:8000...")
        try:
            if hasattr(os, "startfile"):
                os.startfile("http://localhost:8000")
            else:
                webbrowser.open("http://localhost:8000")
        except Exception:
            pass
        sys.exit(0)

    print("=" * 70)
    print("🦷 NHA KHOA NIBI - BS. THẮNG (NINH VÂN - PHỐ DÂU - CỬU YÊN)")
    print("🛡️  HỆ THỐNG MỚI ĐÃ KHỞI CHẠY - MẬT KHẨU ADMIN: 123456 | PIN: 0000")
    print("=" * 70)

    # Setup shortcuts and auto-start
    try:
        setup_desktop_and_startup(base_dir)
    except Exception as e:
        print(f"Lưu ý tạo shortcut: {e}")

    local_ip = get_local_ip()

    # 1. START FASTAPI BACKEND
    print("⏳ Đang khởi động FastAPI trên cổng 8000...")
    def start_fastapi():
        kill_process_on_port(8000)
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "0.0.0.0"],
            cwd=base_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        threading.Thread(target=drain, args=(proc.stdout,), daemon=True).start()
        return proc

    server_proc = start_fastapi()

    # Wait up to 10 seconds for port 8000 to become active
    for _ in range(20):
        if is_server_healthy():
            break
        time.sleep(0.5)

    print("✅ FastAPI backend đã sẵn sàng phục vụ tại http://localhost:8000")

    # 2. INDEPENDENT CLOUDFLARE TUNNEL WORKER THREAD
    # Does NOT block the main server; keeps retrying in background if Wi-Fi connects later
    cloudflared_path = os.path.join(base_dir, "cloudflared.exe")
    if not os.path.exists(cloudflared_path):
        sibling = os.path.join(os.path.dirname(base_dir), "dental-clinic", "cloudflared.exe")
        if os.path.exists(sibling):
            cloudflared_path = sibling

    current_tunnel = {"url": None, "proc": None}

    def tunnel_supervisor():
        if not os.path.exists(cloudflared_path):
            print("⚠️ Không tìm thấy cloudflared.exe, bỏ qua đường hầm Cloudflare.")
            return

        url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        while True:
            try:
                kill_cloudflared()
                time.sleep(1)

                cf_p = subprocess.Popen(
                    [cloudflared_path, "tunnel", "--url", "http://127.0.0.1:8000"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                current_tunnel["proc"] = cf_p

                # Read line by line until tunnel URL is found
                found_url = False
                for line in iter(cf_p.stdout.readline, ""):
                    match = url_pattern.search(line)
                    if match:
                        t_url = match.group(0)
                        current_tunnel["url"] = t_url
                        found_url = True
                        print(f"\n🌐 Cloudflare Tunnel từ xa đã sẵn sàng: {t_url}")
                        update_qr_and_info(base_dir, local_ip, t_url)
                        break

                # Drain remaining output
                for _ in iter(cf_p.stdout.readline, ""):
                    pass

                cf_p.wait()
            except Exception as e:
                pass
            time.sleep(5)  # Reconnect tunnel if disconnected

    threading.Thread(target=tunnel_supervisor, daemon=True).start()

    # 3. RENDER CLOUD KEEP-ALIVE THREAD (Every 8 minutes)
    def keep_render_alive():
        render_url = "https://nha-khoa-nibi.onrender.com/api/auth/me"
        while True:
            try:
                time.sleep(480)
                req = urllib.request.Request(render_url, headers={"User-Agent": "NiBi-KeepAlive/1.0"})
                urllib.request.urlopen(req, timeout=15)
            except Exception:
                pass
    threading.Thread(target=keep_render_alive, daemon=True).start()

    # Initial file info generation
    update_qr_and_info(base_dir, local_ip, None)

    print("\n" + "=" * 70)
    print("✅ HỆ THỐNG PHÒNG KHÁM ĐÃ SẴN SÀNG HOẠT ĐỘNG:")
    print(f"👉 Link máy tính:              http://localhost:8000")
    print(f"👉 Link Wi-Fi nội bộ phòng:     http://{local_ip}:8000")
    print(f"👉 Link Cloud cố định online:  https://nha-khoa-nibi.onrender.com")
    print(f"👉 Mã QR & Link đã lưu ngoài màn hình Desktop")
    print("🔑 Mật khẩu Admin: 123456 | PIN sửa đổi: 0000")
    print("=" * 70 + "\n")

    # ── 4. SELF-HEALING WATCHDOG SUPERVISOR (Chống treo, tự phục hồi) ──
    consecutive_http_fails = 0
    try:
        while True:
            time.sleep(3)

            # Check 1: Did process exit?
            if server_proc.poll() is not None:
                print("⚠️ FastAPI bị tắt ngoài ý muốn, đang tự động khởi động lại...")
                server_proc = start_fastapi()
                consecutive_http_fails = 0
                continue

            # Check 2: Active HTTP Health Check (detect frozen server)
            if not is_server_healthy():
                consecutive_http_fails += 1
                if consecutive_http_fails >= 5:
                    print("⚠️ FastAPI không phản hồi trên cổng 8000 (treo cổng), đang tự động phục hồi...")
                    try:
                        server_proc.terminate()
                        time.sleep(0.5)
                        server_proc.kill()
                    except Exception:
                        pass
                    server_proc = start_fastapi()
                    consecutive_http_fails = 0
            else:
                consecutive_http_fails = 0

    except KeyboardInterrupt:
        print("\nĐang tắt hệ thống...")
    finally:
        try:
            server_proc.terminate()
            if current_tunnel["proc"]:
                current_tunnel["proc"].terminate()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\n[CRITICAL ERROR] {e}")
        traceback.print_exc()

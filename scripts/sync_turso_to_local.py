"""
Script đồng bộ dữ liệu từ Turso Cloud về file SQLite nội bộ trên máy tính (data/nibi.db).
Đảm bảo máy tính phòng khám luôn luôn giữ một bản sao lưu (backup) đầy đủ 100% dữ liệu gốc.
"""
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import sqlite3
import libsql_client

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "nibi.db")
BACKUP_PATH = os.path.join(BASE_DIR, "data", "nibi_backup.db")

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "https://nha-khoa-nibi-nknibi.aws-ap-southeast-1.turso.io")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTQwMjIyNzMsImlkIjoiMDFhMWI0MGUtODMwMS03ZWNiLWE4ODItNTk2MGQyZTljZDIyIiwia2lkIjoicl9rc3pzNWJLSTVUU1phWUQ1X20zNkE5bDNzb2hlOEY5c2JwSVRYVnJPZyIsInJpZCI6Ijg2MTZhMjZhLTMxMDgtNDQ0OS04Y2Y3LTdkNmEwNTA0ODQ0YiJ9.OHTPIylkZHvQr3Av3xiZqqVJXsOhHah0pvP-Cdy1t_oTm02Q2cMY8rY-8qYH0gc-v4UiT3kgwFFmVzuO8d1YCA")

def sync_cloud_to_local():
    print("⏳ Đang đồng bộ dữ liệu từ Turso Cloud về máy tính phòng khám...")
    try:
        client = libsql_client.create_client_sync(TURSO_URL, auth_token=TURSO_TOKEN)
    except Exception as e:
        print(f"❌ Không thể kết nối Turso: {e}")
        return False

    tables = ["users", "sessions", "settings", "patients", "treatments", "payments", "appointments", "audit_logs"]

    local_conn = sqlite3.connect(DB_PATH)
    local_cur = local_conn.cursor()

    try:
        # Disable foreign keys temporarily during sync
        local_cur.execute("PRAGMA foreign_keys=OFF")

        for t in tables:
            # Fetch from Turso
            res = client.execute(f'SELECT * FROM "{t}"')
            cols = res.columns
            rows = res.rows

            # Clear local table
            local_cur.execute(f'DELETE FROM "{t}"')

            # Insert cloud rows into local
            if rows:
                placeholders = ", ".join(["?"] * len(cols))
                col_str = ", ".join([f'"{c}"' for c in cols])
                insert_sql = f'INSERT INTO "{t}" ({col_str}) VALUES ({placeholders})'
                local_cur.executemany(insert_sql, [list(r) for r in rows])

            print(f"  ✅ Bảng {t}: {len(rows)} dòng")

        local_conn.commit()
        local_cur.execute("PRAGMA foreign_keys=ON")
        print("🎉 Đồng bộ hoàn tất! File dữ liệu gốc trên máy tính đã được cập nhật mới nhất.")
        return True
    except Exception as e:
        print(f"❌ Lỗi trong quá trình đồng bộ: {e}")
        local_conn.rollback()
        return False
    finally:
        client.close()
        local_conn.close()

if __name__ == "__main__":
    sync_cloud_to_local()

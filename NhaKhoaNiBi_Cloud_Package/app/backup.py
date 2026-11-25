"""
Nha Khoa NiBi - BS. Thắng
Hệ thống Tự Động Sao Lưu Hàng Tuần & Khôi Phục Cơ Sở Dữ Liệu Toàn Diện
- Tự động sao lưu 1 tuần 1 lần vào Thứ Hai.
- Cơ chế xoay vòng: Khi có bản mới, tự động xóa bản sao lưu cũ để tránh nặng máy (giữ 1 bản mới nhất).
- Hỗ trợ sao lưu ngay, tải file về máy và khôi phục hoàn toàn dữ liệu từ bản sao lưu.
"""
import asyncio
import datetime as dt
import json
import logging
import os
import shutil
import sqlite3
import time
from typing import Optional

from . import db as dbm
from .db import SCHEMA, db

logger = logging.getLogger("nibi.backup")

BACKUP_DIR = os.path.join(dbm.BASE_DIR, "data", "backups")
INFO_FILE = os.path.join(BACKUP_DIR, "backup_info.json")

TABLES_TO_BACKUP = [
    "users",
    "settings",
    "patients",
    "treatments",
    "payments",
    "appointments",
    "audit_logs",
    "supplies",
    "supply_transactions",
]

TABLES_CLEAR_ORDER = [
    "supply_transactions",
    "supplies",
    "appointments",
    "payments",
    "treatments",
    "patients",
    "settings",
    "sessions",
    "audit_logs",
    "users",
]


def ensure_backup_dir() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def get_latest_backup_path() -> Optional[str]:
    ensure_backup_dir()
    files = [
        os.path.join(BACKUP_DIR, f)
        for f in os.listdir(BACKUP_DIR)
        if f.startswith("NiBi_Backup_") and f.endswith(".db")
    ]
    if not files:
        return None
    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return files[0]


def get_backup_info() -> dict:
    ensure_backup_dir()
    latest_file = get_latest_backup_path()
    if os.path.exists(INFO_FILE):
        try:
            with open(INFO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if latest_file and os.path.exists(latest_file):
                    data["exists"] = True
                    data["size_kb"] = round(os.path.getsize(latest_file) / 1024, 1)
                    return data
        except Exception:
            pass

    if latest_file and os.path.exists(latest_file):
        size_kb = round(os.path.getsize(latest_file) / 1024, 1)
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(latest_file)).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "exists": True,
            "filename": os.path.basename(latest_file),
            "last_backup_time": mtime,
            "last_backup_date": mtime[:10],
            "size_kb": size_kb,
            "reason": "auto_detected",
            "counts": {},
        }

    return {
        "exists": False,
        "filename": None,
        "last_backup_time": None,
        "last_backup_date": None,
        "size_kb": 0,
        "reason": None,
        "counts": {},
    }


def cleanup_old_backups(keep_latest: int = 1) -> list[str]:
    """Xóa các bản sao lưu cũ để giải phóng dung lượng ổ đĩa, chỉ giữ lại số lượng bản mới nhất."""
    ensure_backup_dir()
    files = [
        os.path.join(BACKUP_DIR, f)
        for f in os.listdir(BACKUP_DIR)
        if f.startswith("NiBi_Backup_") and f.endswith(".db")
    ]
    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    deleted = []
    if len(files) > keep_latest:
        for old in files[keep_latest:]:
            try:
                os.remove(old)
                deleted.append(os.path.basename(old))
                logger.info("Deleted old backup: %s", old)
            except Exception as e:
                logger.warning("Could not delete old backup %s: %s", old, e)
    return deleted


def create_full_backup(reason: str = "manual", triggered_by: str = "admin") -> dict:
    """Sao lưu toàn bộ cơ sở dữ liệu hiện tại (Turso Cloud hoặc SQLite) thành 1 file SQLite hoàn chỉnh."""
    ensure_backup_dir()
    now_str = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    today_str = dt.date.today().isoformat()
    filename = f"NiBi_Backup_{now_str}.db"
    target_path = os.path.join(BACKUP_DIR, filename)

    logger.info("Starting database backup (%s) -> %s", reason, filename)

    with db() as conn:
        dst = sqlite3.connect(target_path)
        try:
            dst.execute("PRAGMA foreign_keys=OFF")
            dst.executescript(SCHEMA)

            table_counts = {}
            for table in TABLES_TO_BACKUP:
                try:
                    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                except Exception as err:
                    logger.warning("Could not read table %s: %s", table, err)
                    continue

                if not rows:
                    table_counts[table] = 0
                    continue

                first = rows[0]
                cols = list(first.keys()) if hasattr(first, "keys") else [d[0] for d in conn.description]
                col_list_str = ",".join(f'"{c}"' for c in cols)
                placeholders = ",".join("?" * len(cols))
                data_to_insert = [tuple(r[c] for c in cols) for r in rows]

                dst.executemany(f"INSERT OR REPLACE INTO {table} ({col_list_str}) VALUES ({placeholders})", data_to_insert)
                table_counts[table] = len(rows)

            dst.execute("PRAGMA foreign_keys=ON")
            dst.commit()
        finally:
            dst.close()

    # Dọn dẹp bản sao lưu cũ (chỉ giữ 1 bản mới nhất)
    deleted_old = cleanup_old_backups(keep_latest=1)

    size_kb = round(os.path.getsize(target_path) / 1024, 1)
    backup_time_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    info = {
        "exists": True,
        "filename": filename,
        "last_backup_time": backup_time_str,
        "last_backup_date": today_str,
        "size_kb": size_kb,
        "reason": reason,
        "triggered_by": triggered_by,
        "counts": table_counts,
        "deleted_old_backups": deleted_old,
    }

    try:
        with open(INFO_FILE, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to write backup info file: %s", e)

    logger.info("Backup completed successfully: %s (size: %s KB, patients: %d, treatments: %d)",
                filename, size_kb, table_counts.get("patients", 0), table_counts.get("treatments", 0))

    return info


def restore_full_backup(backup_sqlite_path: str, user: dict) -> dict:
    """Khôi phục hoàn toàn cơ sở dữ liệu từ một file sao lưu SQLite."""
    if not os.path.exists(backup_sqlite_path):
        raise FileNotFoundError("Không tìm thấy file sao lưu để khôi phục")

    src = sqlite3.connect(backup_sqlite_path)
    src.row_factory = sqlite3.Row

    try:
        # Kiểm tra tính toàn vẹn của file sao lưu
        chk = src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='patients'").fetchone()
        if not chk:
            raise ValueError("File sao lưu không hợp lệ: Không tìm thấy bảng bệnh nhân")

        restored_counts = {}

        with db() as conn:
            # Xóa các bảng dữ liệu cũ
            for t in TABLES_CLEAR_ORDER:
                try:
                    conn.execute(f"DELETE FROM {t}")
                except Exception as e:
                    logger.warning("Clear table %s warning: %s", t, e)

            # Nạp dữ liệu từ file sao lưu sang cơ sở dữ liệu hoạt động
            for table in TABLES_TO_BACKUP:
                chk_t = src.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'").fetchone()
                if not chk_t:
                    continue

                rows = src.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    restored_counts[table] = 0
                    continue

                first = rows[0]
                cols = list(first.keys())
                col_list_str = ",".join(f'"{c}"' for c in cols)
                placeholders = ",".join("?" * len(cols))
                data_to_insert = [tuple(r[c] for c in cols) for r in rows]

                # Chia chunk 100 dòng để tối ưu và không bị nghẽn payload
                chunk_size = 100
                for i in range(0, len(data_to_insert), chunk_size):
                    chunk = data_to_insert[i:i + chunk_size]
                    conn.executemany(f"INSERT INTO {table} ({col_list_str}) VALUES ({placeholders})", chunk)

                restored_counts[table] = len(rows)

            # Ghi vết vào nhật ký bảo mật
            conn.execute(
                "INSERT INTO audit_logs(username,full_name,action,entity,entity_id,patient_id,patient_name,summary,changes,is_read)"
                " VALUES(?,?,?,?,?,?,?,?,?,1)",
                (
                    user.get("username", "admin"),
                    user.get("full_name", "Quản trị viên"),
                    "database_restore",
                    "system",
                    0,
                    None,
                    "",
                    f"Khôi phục hoàn toàn cơ sở dữ liệu từ file {os.path.basename(backup_sqlite_path)}: {restored_counts.get('patients', 0)} bệnh nhân, {restored_counts.get('treatments', 0)} đợt điều trị",
                    json.dumps(restored_counts, ensure_ascii=False),
                ),
            )

    finally:
        src.close()

    logger.info("Database restore finished successfully: %s", restored_counts)
    return restored_counts


def check_and_run_weekly_backup() -> Optional[dict]:
    """
    Kiểm tra lịch trình tự động:
    - 1 tuần 1 lần vào Thứ Hai (Monday: weekday() == 0).
    - Hoặc nếu lần sao lưu cuối cùng đã quá 7 ngày (phòng trường hợp máy tính tắt vào thứ Hai).
    """
    today = dt.date.today()
    info = get_backup_info()
    last_date_str = info.get("last_backup_date")

    should_backup = False
    reason = "weekly_monday"

    if not info.get("exists") or not last_date_str:
        should_backup = True
        reason = "initial_backup"
    else:
        try:
            last_date = dt.date.fromisoformat(last_date_str)
            days_since = (today - last_date).days

            # Thứ Hai (weekday == 0) và hôm nay chưa sao lưu
            if today.weekday() == 0 and days_since >= 1:
                should_backup = True
                reason = "weekly_monday"
            # Hoặc đã quá 7 ngày kể từ lần sao lưu gần nhất
            elif days_since >= 7:
                should_backup = True
                reason = "catchup_7days"
        except Exception:
            should_backup = True

    if should_backup:
        logger.info("Automatic weekly backup triggered: reason=%s", reason)
        return create_full_backup(reason=reason, triggered_by="auto_scheduler")

    return None


async def start_backup_scheduler():
    """Vòng lặp ngầm chạy định kỳ kiểm tra và tự động sao lưu vào Thứ Hai hàng tuần."""
    while True:
        try:
            check_and_run_weekly_backup()
        except Exception as e:
            logger.warning("Backup scheduler check error: %s", e)
        # Kiểm tra lại mỗi 3600 giây (1 giờ)
        await asyncio.sleep(3600)

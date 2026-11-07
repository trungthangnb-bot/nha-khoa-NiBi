import sqlite3
import os
import sys
import libsql_client

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "https://nha-khoa-nibi-nknibi.aws-ap-southeast-1.turso.io")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTQwMjIyNzMsImlkIjoiMDFhMWI0MGUtODMwMS03ZWNiLWE4ODItNTk2MGQyZTljZDIyIiwia2lkIjoicl9rc3pzNWJLSTVUU1phWUQ1X20zNkE5bDNzb2hlOEY5c2JwSVRYVnJPZyIsInJpZCI6Ijg2MTZhMjZhLTMxMDgtNDQ0OS04Y2Y3LTdkNmEwNTA0ODQ0YiJ9.OHTPIylkZHvQr3Av3xiZqqVJXsOhHah0pvP-Cdy1t_oTm02Q2cMY8rY-8qYH0gc-v4UiT3kgwFFmVzuO8d1YCA")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "nibi.db")

print(f"Connecting to Turso: {TURSO_URL}...")
client = libsql_client.create_client_sync(TURSO_URL, auth_token=TURSO_TOKEN)

local_conn = sqlite3.connect(DB_PATH)
local_cur = local_conn.cursor()

# 1. Fetch all table names in nibi.db in dependency order
tables = ["users", "sessions", "settings", "patients", "treatments", "payments", "appointments", "audit_logs"]

# 2. Get table definitions
print("Creating tables on Turso...")
for t in tables:
    r = local_cur.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{t}'").fetchone()
    if r and r[0]:
        sql = r[0]
        # Replace CREATE TABLE with CREATE TABLE IF NOT EXISTS
        if not "IF NOT EXISTS" in sql.upper():
            sql = sql.replace(f"CREATE TABLE {t}", f"CREATE TABLE IF NOT EXISTS {t}")
            sql = sql.replace(f'CREATE TABLE "{t}"', f'CREATE TABLE IF NOT EXISTS "{t}"')
        client.execute(sql)
        print(f"  Created table: {t}")

# 3. Create indices
for r in local_cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall():
    client.execute(r[0])
print("  Created indices.")

# 4. Copy rows
print("\nMigrating rows to Turso...")
for t in tables:
    rows = local_cur.execute(f'SELECT * FROM "{t}"').fetchall()
    cols = [d[0] for d in local_cur.description]
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join([f'"{c}"' for c in cols])
    insert_sql = f'INSERT OR REPLACE INTO "{t}" ({col_str}) VALUES ({placeholders})'
    
    # Batch insert in chunks of 50
    chunk_size = 50
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        stmts = [libsql_client.Statement(insert_sql, list(row)) for row in chunk]
        client.batch(stmts)
    print(f"  Migrated {len(rows)} rows into {t}")

# 5. Verify row counts on Turso
print("\nVerifying row counts on Turso:")
for t in tables:
    res = client.execute(f'SELECT COUNT(*) FROM "{t}"')
    cnt = res.rows[0][0]
    print(f"  {t}: {cnt} rows")

client.close()
local_conn.close()
print("\nMigration to Turso completed successfully!")

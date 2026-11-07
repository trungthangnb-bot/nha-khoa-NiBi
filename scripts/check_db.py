import sqlite3
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "nibi.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
print("Tables in nibi.db:")
for t in tables:
    cnt = cursor.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print(f"  {t}: {cnt} rows")

schema_sql = []
for r in cursor.execute("SELECT sql FROM sqlite_master WHERE type in ('table', 'index') AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"):
    schema_sql.append(r[0])

print(f"\nTotal schema definitions: {len(schema_sql)}")

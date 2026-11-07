import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import libsql_client

TURSO_URL = "https://nha-khoa-nibi-nknibi.aws-ap-southeast-1.turso.io"
TURSO_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTQwMjIyNzMsImlkIjoiMDFhMWI0MGUtODMwMS03ZWNiLWE4ODItNTk2MGQyZTljZDIyIiwia2lkIjoicl9rc3pzNWJLSTVUU1phWUQ1X20zNkE5bDNzb2hlOEY5c2JwSVRYVnJPZyIsInJpZCI6Ijg2MTZhMjZhLTMxMDgtNDQ0OS04Y2Y3LTdkNmEwNTA0ODQ0YiJ9.OHTPIylkZHvQr3Av3xiZqqVJXsOhHah0pvP-Cdy1t_oTm02Q2cMY8rY-8qYH0gc-v4UiT3kgwFFmVzuO8d1YCA"

class TursoRow(dict):
    def __init__(self, cols, vals):
        super().__init__(zip(cols, vals))
        self._vals = tuple(vals)
        self._cols = tuple(cols)
    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        return super().__getitem__(k)
    def keys(self):
        return self._cols

class TursoCursor:
    def __init__(self, result_set):
        self._res = result_set
        self._rows = [TursoRow(result_set.columns, row) for row in result_set.rows] if result_set else []
        self._idx = 0
        self.lastrowid = getattr(result_set, 'last_insert_rowid', None)
        self.rowcount = getattr(result_set, 'rows_affected', 0)
    def fetchone(self):
        if self._idx < len(self._rows):
            r = self._rows[self._idx]
            self._idx += 1
            return r
        return None
    def fetchall(self):
        r = self._rows[self._idx:]
        self._idx = len(self._rows)
        return r
    def __iter__(self):
        return iter(self._rows)

class TursoConnection:
    def __init__(self, client):
        self._client = client
    def execute(self, sql, params=None):
        if params is None:
            res = self._client.execute(sql)
        else:
            res = self._client.execute(sql, list(params))
        return TursoCursor(res)
    def executemany(self, sql, seq_of_params):
        stmts = [libsql_client.Statement(sql, list(p)) for p in seq_of_params]
        res_list = self._client.batch(stmts)
        return TursoCursor(res_list[-1] if res_list else None)
    def executescript(self, script):
        for s in script.split(";"):
            s = s.strip()
            if s:
                self._client.execute(s)
    def commit(self):
        pass
    def rollback(self):
        pass
    def close(self):
        pass

client = libsql_client.create_client_sync(TURSO_URL, auth_token=TURSO_TOKEN)
conn = TursoConnection(client)

# Test 1: Fetch users
cur = conn.execute("SELECT id, username, full_name, role FROM users")
rows = cur.fetchall()
print(f"Test 1 - Users count: {len(rows)}")
for r in rows:
    print(f"  {r['id']}: {r['username']} ({r['role']}) - as dict: {dict(r)}")

# Test 2: Fetch patients with param
cur = conn.execute("SELECT id, code, name, phone FROM patients WHERE branch = ? ORDER BY id DESC LIMIT 3", ["Ninh Vân"])
rows = cur.fetchall()
print(f"\nTest 2 - Patients: {len(rows)}")
for r in rows:
    print(f"  {r['code']}: {r['name']} - index access: r[0]={r[0]}, r[1]={r[1]}")

# Test 3: Insert test and lastrowid
cur = conn.execute("INSERT INTO settings(key, value) VALUES(?, ?)", ["test_sync_key", "turso_works"])
print(f"\nTest 3 - Insert setting affected: {cur.rowcount}")

# Test 4: Delete test
cur = conn.execute("DELETE FROM settings WHERE key = ?", ["test_sync_key"])
print(f"Test 4 - Delete affected: {cur.rowcount}")

print("\nAll Turso wrapper tests passed!")

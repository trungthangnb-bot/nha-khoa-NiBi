import datetime as dt
import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp()
os.environ["NIBI_DB"] = os.path.join(_tmp, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

TODAY = dt.date.today()
iso = lambda d: d.isoformat()  # noqa: E731


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cm = TestClient(app)
        cls.c = cls.cm.__enter__()
        cls.admin = cls.login("admin", "123456")
        r = cls.c.post("/api/admin/users", headers=cls.admin,
                       json={"username": "letan", "full_name": "Lễ tân", "password": "abc123", "role": "staff"})
        assert r.status_code == 200, r.text
        cls.staff = cls.login("letan", "abc123")
        cls.c.post("/api/admin/users", headers=cls.admin,
                   json={"username": "letan2", "full_name": "Lễ tân 2", "password": "abc123", "role": "staff"})
        cls.staff2 = cls.login("letan2", "abc123")

    @classmethod
    def tearDownClass(cls):
        cls.cm.__exit__(None, None, None)

    @classmethod
    def login(cls, u, p):
        r = cls.c.post("/api/auth/login", json={"username": u, "password": p})
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["token"]}

    def new_patient(self, name="Nguyễn Đức Thắng", **kw):
        body = {"name": name, "phone": "0912345678", "yob": 1990, "address": "Phú Lăng", "branch": "Ninh Vân", **kw}
        r = self.c.post("/api/patients", headers=self.staff, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ── auth ──
    def test_01_login_rules(self):
        r = self.c.post("/api/auth/login", json={"username": "admin", "password": "sai"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.c.get("/api/patients").status_code, 401)
        self.assertEqual(self.c.get("/api/auth/me", headers=self.admin).json()["role"], "admin")

    def test_02_staff_cannot_use_admin_endpoints(self):
        for path in ("/api/admin/reports", "/api/admin/audit", "/api/admin/users", "/api/export/patients.csv"):
            self.assertEqual(self.c.get(path, headers=self.staff).status_code, 403, path)

    # ── patients / search ──
    def test_10_create_with_first_treatment_and_totals(self):
        nxt = iso(TODAY + dt.timedelta(days=7))
        res = self.new_patient("Trần Thị Bích Ngọc", first_treatment={
            "procedure": "nhổ r38 + crvl", "category": "Nhổ răng", "cost": 500_000, "paid": 300_000,
            "method": "Chuyển khoản", "next_date": nxt})
        d = self.c.get(f"/api/patients/{res['id']}", headers=self.staff).json()
        self.assertEqual(d["totals"], {"billed": 500_000, "paid": 300_000, "debt": 200_000})
        self.assertEqual(len(d["appointments"]), 1)
        self.assertEqual(d["appointments"][0]["appt_date"], nxt)
        self.assertEqual(d["patient"]["code"], f"BN{res['id']:05d}")
        self.assertEqual(d["patient"]["age"], TODAY.year - 1990)

    def test_11_overpaying_first_treatment_rejected(self):
        r = self.c.post("/api/patients", headers=self.staff, json={
            "name": "Khách Test Quá Tiền", "branch": "Ninh Vân",
            "first_treatment": {"procedure": "hàn", "cost": 100_000, "paid": 150_000}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.get("/api/patients?q=qua tien", headers=self.staff).json()["total"], 0,
                         "failed intake must not leave a half-created patient")

    def test_12_search_ignores_accents_and_matches_phone(self):
        self.new_patient("Đỗ Văn Tuấn Anh", phone="0987000111", yob=1985)
        for q in ("do van tuan", "ĐỖ VĂN", "tuan anh", "0987000111", "987000"):
            self.assertGreaterEqual(self.c.get("/api/patients", params={"q": q}, headers=self.staff).json()["total"],
                                    1, q)
        self.assertEqual(self.c.get("/api/patients", params={"q": "khong ton tai xyz"},
                                    headers=self.staff).json()["total"], 0)

    def test_13_search_is_not_sql_injectable(self):
        r = self.c.get("/api/patients", params={"q": "' OR 1=1 --"}, headers=self.staff)
        self.assertEqual((r.status_code, r.json()["total"]), (200, 0))
        r = self.c.get("/api/patients", params={"q": "%"}, headers=self.staff)
        self.assertEqual(r.json()["total"], 0, "'%' must be matched literally")

    def test_14_duplicate_detected_and_forced(self):
        self.new_patient("Phạm Duplicate Một", yob=1977, phone="")
        r = self.c.post("/api/patients", headers=self.staff,
                        json={"name": "pham duplicate mot", "yob": 1977, "branch": "Ninh Vân"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("existing_id", r.json()["detail"])
        r = self.c.post("/api/patients", headers=self.staff,
                        json={"name": "pham duplicate mot", "yob": 1977, "branch": "Ninh Vân", "force": True})
        self.assertEqual(r.status_code, 200)

    def test_15_validation(self):
        bad = [{"name": "  ", "branch": "Ninh Vân"}, {"name": "A", "branch": "Hà Nội"},
               {"name": "A", "branch": "Ninh Vân", "yob": 1800}]
        for body in bad:
            self.assertEqual(self.c.post("/api/patients", headers=self.staff, json=body).status_code, 422, body)

    # ── PIN, edits, audit ──
    def test_20_edit_requires_pin_and_is_audited(self):
        p = self.new_patient("Lê Sửa Hồ Sơ")["id"]
        body = {"name": "Lê Sửa Hồ Sơ", "phone": "0900000001", "yob": 1991, "address": "Xí Nghiệp",
                "branch": "Ninh Vân", "medical_history": "dị ứng thuốc tê", "notes": ""}
        self.assertEqual(self.c.put(f"/api/patients/{p}", headers=self.staff, json=body).status_code, 403)
        self.assertEqual(self.c.put(f"/api/patients/{p}", headers=self.staff, json={**body, "pin": "1111"}).status_code, 403)
        r = self.c.put(f"/api/patients/{p}", headers=self.staff, json={**body, "pin": "0000"})
        self.assertEqual((r.status_code, r.json()["changed"]), (200, 4))
        log = self.c.get("/api/admin/audit", headers=self.admin).json()["items"]
        entry = next(x for x in log if x["action"] == "patient_edit" and x["patient_id"] == p)
        self.assertEqual(entry["username"], "letan")
        self.assertIn("SĐT", {c["label"] for c in entry["changes"]})
        # searching by the new phone works => search_text refreshed
        self.assertEqual(self.c.get("/api/patients?q=0900000001", headers=self.staff).json()["total"], 1)

    def test_21_pin_bruteforce_is_locked(self):
        codes = [self.c.post("/api/pin/verify", headers=self.staff2, json={"pin": str(1000 + i)}).status_code
                 for i in range(8)]
        self.assertEqual(codes[:6], [403] * 6)
        self.assertEqual(codes[6], 429)
        # correct PIN is also refused while locked
        self.assertEqual(self.c.post("/api/pin/verify", headers=self.staff2, json={"pin": "0000"}).status_code, 429)

    def test_22_treatment_edit_delete_and_payment_rules(self):
        pid = self.new_patient("Võ Điều Trị Sửa", first_treatment={
            "procedure": "tủy r36", "category": "Điều trị tủy", "cost": 400_000, "paid": 400_000})["id"]
        t = self.c.get(f"/api/patients/{pid}", headers=self.staff).json()["treatments"][0]
        base = {"visit_date": t["visit_date"], "branch": "Ninh Vân", "category": "Điều trị tủy", "dentist": "Thắng",
                "procedure": "tủy r36", "tooth": "36", "notes": "", "pin": "0000"}
        r = self.c.put(f"/api/treatments/{t['id']}", headers=self.staff, json={**base, "cost": 300_000})
        self.assertEqual(r.status_code, 422, "cost below what was already paid must be refused")
        r = self.c.put(f"/api/treatments/{t['id']}", headers=self.staff, json={**base, "cost": 450_000, "reason": "thêm vật liệu"})
        self.assertEqual(r.status_code, 200)
        entry = next(x for x in self.c.get("/api/admin/audit", headers=self.admin).json()["items"]
                     if x["action"] == "treatment_edit" and x["patient_id"] == pid)
        cost = next(c for c in entry["changes"] if c["field"] == "cost")
        self.assertEqual((cost["old"], cost["new"]), (400_000, 450_000))
        self.assertIn("thêm vật liệu", entry["summary"])
        d = self.c.get(f"/api/patients/{pid}", headers=self.staff).json()
        self.assertEqual(d["totals"]["debt"], 50_000)
        pay_id = d["treatments"][0]["payments"][0]["id"]
        self.assertEqual(self.c.post(f"/api/payments/{pay_id}/delete", headers=self.staff, json={}).status_code, 403)
        self.assertEqual(self.c.post(f"/api/payments/{pay_id}/delete", headers=self.staff, json={"pin": "0000"}).status_code, 200)
        self.assertEqual(self.c.get(f"/api/patients/{pid}", headers=self.staff).json()["totals"]["debt"], 450_000)

    # ── delete patient ──
    def test_30_only_admin_with_password_can_delete(self):
        pid = self.new_patient("Hồ Sơ Cần Xóa", first_treatment={"procedure": "hàn", "cost": 100_000, "paid": 0})["id"]
        url = f"/api/patients/{pid}/delete"
        self.assertEqual(self.c.post(url, headers=self.staff, json={"password": "123456"}).status_code, 403)
        self.assertEqual(self.c.post(url, headers=self.admin, json={"password": "sai"}).status_code, 403)
        self.assertEqual(self.c.get(f"/api/patients/{pid}", headers=self.admin).status_code, 200)
        self.assertEqual(self.c.post(url, headers=self.admin, json={"password": "123456"}).status_code, 200)
        self.assertEqual(self.c.get(f"/api/patients/{pid}", headers=self.admin).status_code, 404)
        entry = next(x for x in self.c.get("/api/admin/audit", headers=self.admin).json()["items"]
                     if x["action"] == "patient_delete" and x["patient_id"] == pid)
        self.assertIn("Hồ Sơ Cần Xóa", entry["changes"][0]["old"], "snapshot kept for the owner")
        self.assertEqual(self.c.get("/api/debts?q=can xoa", headers=self.admin).json()["total"], 0,
                         "debts of a deleted patient must vanish (cascade)")

    # ── debts ──
    def test_40_collect_applies_oldest_first_and_caps(self):
        pid = self.new_patient("Bùi Nợ Hai Đợt")["id"]
        for date, cost in ((iso(TODAY - dt.timedelta(days=60)), 1_000_000), (iso(TODAY - dt.timedelta(days=10)), 500_000)):
            r = self.c.post(f"/api/patients/{pid}/treatments", headers=self.staff, json={
                "procedure": "răng sứ", "category": "Răng giả", "cost": cost, "paid": 0, "visit_date": date})
            self.assertEqual(r.status_code, 200)
        r = self.c.post(f"/api/patients/{pid}/collect", headers=self.staff, json={"amount": 1_200_000})
        self.assertEqual(r.json()["remaining_debt"], 300_000)
        applied = r.json()["applied"]
        self.assertEqual([a["amount"] for a in applied], [1_000_000, 200_000])
        self.assertEqual(self.c.post(f"/api/patients/{pid}/collect", headers=self.staff,
                                     json={"amount": 300_001}).status_code, 422)
        debt = self.c.get("/api/debts?q=bui no hai", headers=self.staff).json()
        self.assertEqual((debt["total"], debt["totals"]["debt"]), (1, 300_000))
        self.assertEqual(self.c.post(f"/api/patients/{pid}/collect", headers=self.staff, json={"amount": 300_000}).status_code, 200)
        self.assertEqual(self.c.get("/api/debts?q=bui no hai", headers=self.staff).json()["total"], 0)

    def test_41_staff_can_read_debts(self):
        r = self.c.get("/api/debts", headers=self.staff)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["totals"]["debt"], 0)

    # ── appointments ──
    def test_50_appointment_buckets(self):
        pid = self.new_patient("Hẹn Lịch Thử")["id"]
        mk = lambda d, kind="hen_kham": self.c.post("/api/appointments", headers=self.staff, json={  # noqa: E731
            "patient_id": pid, "kind": kind, "appt_date": iso(d), "appt_time": "09:30", "reason": "kiểm tra"}).json()["id"]
        a_today, a_future, a_past = mk(TODAY), mk(TODAY + dt.timedelta(days=3)), mk(TODAY - dt.timedelta(days=5))
        get = lambda when: {x["id"] for x in self.c.get(  # noqa: E731
            f"/api/appointments?kind=hen_kham&when={when}&q=hen lich", headers=self.staff).json()["items"]}
        self.assertEqual(get("today"), {a_today})
        self.assertEqual(get("upcoming"), {a_today, a_future})
        self.assertEqual(get("overdue"), {a_past})
        self.c.put(f"/api/appointments/{a_past}", headers=self.staff, json={"status": "Lỡ hẹn"})
        self.assertEqual(get("overdue"), set())
        self.assertEqual(get("done"), {a_past})
        self.assertEqual(self.c.post("/api/appointments", headers=self.staff, json={
            "patient_id": pid, "appt_date": "2026-13-45"}).status_code, 422)

    # ── dashboard / reports ──
    def test_60_revenue_visible_only_to_admin(self):
        # Staff is strictly blocked from dashboard (403 Forbidden)
        self.assertEqual(self.c.get("/api/dashboard", headers=self.staff).status_code, 403)
        # Admin has full access
        res = self.c.get("/api/dashboard", headers=self.admin)
        self.assertEqual(res.status_code, 200)
        rev = res.json()["revenue"]
        self.assertEqual(len(rev["series"]), 12)
        self.assertGreater(rev["month"], 0)

    def test_61_reports_reconcile_with_patient_ledger(self):
        rep = self.c.get(f"/api/admin/reports?year={TODAY.year}", headers=self.admin).json()
        month = next(m for m in rep["months"] if m["month"] == TODAY.strftime("%Y-%m"))
        ledger = self.c.get("/api/patients?limit=200", headers=self.admin).json()["items"]
        self.assertGreaterEqual(month["collected"], 0)
        self.assertLessEqual(month["collected"], sum(p["paid"] for p in ledger))

    def test_62_change_password_flow(self):
        r = self.c.post("/api/auth/change-password", headers=self.staff2, json={"old_password": "abc123", "new_password": "xyz789"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.post("/api/auth/login", json={"username": "letan2", "password": "abc123"}).status_code, 401)
        self.assertEqual(self.c.post("/api/auth/login", json={"username": "letan2", "password": "xyz789"}).status_code, 200)

    def test_63_clinical_recall_rules_denture_and_ortho(self):
        # 1. Test "Răng giả" recall auto-generation (+6m and +1y)
        p1 = self.new_patient("BN Răng Giả", phone="0911000111")
        t1 = self.c.post(f"/api/patients/{p1['id']}/treatments", headers=self.staff, json={
            "visit_date": "2026-01-15", "branch": "Ninh Vân", "category": "Răng giả",
            "procedure": "Làm hàm giả tháo lắp bán phần", "cost": 5000000, "paid": 5000000, "notes": ""
        }).json()
        self.assertIn("id", t1)

        # Check appointments created for p1
        appts1 = self.c.get(f"/api/appointments?when=all&q=BN%20R%C4%83ng%20Gi%E1%BA%A3", headers=self.staff).json()["items"]
        dates1 = [a["appt_date"] for a in appts1]
        self.assertIn("2026-07-15", dates1) # 6 months
        self.assertIn("2027-01-15", dates1) # 1 year

        # 2. Test "Chỉnh nha" recall auto-generation (+1m)
        p2 = self.new_patient("BN Chỉnh Nha", phone="0922000222")
        t2 = self.c.post(f"/api/patients/{p2['id']}/treatments", headers=self.staff, json={
            "visit_date": "2026-03-10", "branch": "Phố Dâu", "category": "Chỉnh nha",
            "procedure": "Gắn mắc cài 2 hàm", "cost": 25000000, "paid": 5000000, "notes": ""
        }).json()
        self.assertIn("id", t2)

        appts2 = self.c.get(f"/api/appointments?when=all&q=BN%20Ch%E1%BB%89nh%20Nha", headers=self.staff).json()["items"]
        dates2 = [a["appt_date"] for a in appts2]
        self.assertIn("2026-04-10", dates2) # 1 month

        # When the ortho appointment is marked "Đã đến", next month appointment is automatically queued
        ortho_appt = next(a for a in appts2 if a["appt_date"] == "2026-04-10")
        up = self.c.put(f"/api/appointments/{ortho_appt['id']}", headers=self.staff, json={"status": "Đã đến"}).json()
        self.assertTrue(up["ok"])

        appts2_after = self.c.get(f"/api/appointments?when=all&q=BN%20Ch%E1%BB%89nh%20Nha", headers=self.staff).json()["items"]
        dates2_after = [a["appt_date"] for a in appts2_after]
        self.assertIn("2026-05-10", dates2_after) # Next 1 month automatically scheduled!

    def test_64_appointments_default_current_month(self):
        # Default query without `when` parameter must return `when="month"`
        res = self.c.get("/api/appointments", headers=self.staff).json()
        self.assertIn("month", res["counts"])
        self.assertIn("current_month", res)
        self.assertEqual(res["current_month"], TODAY.strftime("%Y-%m"))

    def test_65_silent_admin_alerts_on_record_edits(self):
        # 1. Staff cannot access alerts
        self.assertEqual(self.c.get("/api/admin/alerts", headers=self.staff).status_code, 403)

        # 2. Staff edits a patient record
        p = self.new_patient("BN Test Cảnh Báo", phone="0933333333")
        r_edit = self.c.put(f"/api/patients/{p['id']}", headers=self.staff, json={
            "name": "BN Test Cảnh Báo Đã Sửa", "branch": "Ninh Vân", "phone": "0988888888", "notes": "Đổi số điện thoại", "pin": "0000"
        })
        self.assertEqual(r_edit.status_code, 200)

        # 3. Admin silently receives alert
        alerts = self.c.get("/api/admin/alerts", headers=self.admin).json()
        self.assertGreaterEqual(alerts["unread"], 1)
        found = any(a["patient_id"] == p["id"] and a["action"] == "patient_edit" for a in alerts["items"])
        self.assertTrue(found)

        # 4. Admin marks read
        res_read = self.c.post("/api/admin/alerts/mark-read", headers=self.admin, json={}).json()
        self.assertTrue(res_read["ok"])
        alerts_after = self.c.get("/api/admin/alerts", headers=self.admin).json()
        self.assertEqual(alerts_after["unread"], 0)

    def test_66_backup_download(self):
        # 1. Unauthenticated download fails
        res_no_auth = self.c.get("/api/admin/backup.db")
        self.assertEqual(res_no_auth.status_code, 401)

        # 2. Staff download is forbidden (403)
        res_staff = self.c.get("/api/admin/backup.db", headers=self.staff)
        self.assertEqual(res_staff.status_code, 403)

        # 3. Admin download with Header succeeds
        res_header = self.c.get("/api/admin/backup.db", headers=self.admin)
        self.assertEqual(res_header.status_code, 200)
        self.assertGreater(len(res_header.content), 1000)

        # 4. Admin download with Query token (?token=...) succeeds
        admin_token = self.admin["Authorization"].split()[1]
        res_query = self.c.get(f"/api/admin/backup.db?token={admin_token}")
        self.assertEqual(res_query.status_code, 200)
        self.assertGreater(len(res_query.content), 1000)

    def test_67_export_excel_workbook(self):
        # 1. Staff is forbidden
        res_staff = self.c.get("/api/admin/export-excel.xlsx", headers=self.staff)
        self.assertEqual(res_staff.status_code, 403)

        # 2. Admin download succeeds and returns valid xlsx
        res_admin = self.c.get("/api/admin/export-excel.xlsx", headers=self.admin)
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.headers["content-type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertGreater(len(res_admin.content), 10000)

        # 3. Query token also succeeds
        admin_token = self.admin["Authorization"].split()[1]
        res_q = self.c.get(f"/api/export/quan-ly-nibi-2026.xlsx?token={admin_token}")
        self.assertEqual(res_q.status_code, 200)
        self.assertGreater(len(res_q.content), 10000)

    def test_70_ai_parse_intake(self):
        text = "Bệnh nhân Trần Thị Lan sinh năm 1995 ở Ninh Vân, sđt 0987654321 nhổ răng R48 hết 500k đã trả 200k tiền mặt, hẹn 7 ngày sau tái khám"
        res = self.c.post("/api/ai/parse-intake", json={"text": text}, headers=self.staff)
        self.assertEqual(res.status_code, 200)
        d = res.json()["data"]
        self.assertIn("Trần Thị Lan", d["name"])
        self.assertEqual(d["phone"], "0987654321")
        self.assertEqual(d["yob"], 1995)
        self.assertEqual(d["address"], "Ninh Vân")
        self.assertEqual(d["cost"], 500000)
        self.assertEqual(d["paid"], 200000)
        self.assertEqual(d["method"], "Tiền mặt")
        self.assertTrue(bool(d["next_date"]))

    def test_71_ai_chat_query(self):
        # 1. Staff asking for revenue -> BLOCKED (RBAC)
        res_staff_rev = self.c.post("/api/ai/chat", json={"message": "Doanh thu tháng này thế nào?"}, headers=self.staff)
        self.assertEqual(res_staff_rev.status_code, 200)
        self.assertIn("Bảo Mật Tài Chính", res_staff_rev.json()["reply"])

        # 2. Admin asking for revenue -> ALLOWED
        res_admin_rev = self.c.post("/api/ai/chat", json={"message": "Doanh thu tháng này thế nào?"}, headers=self.admin)
        self.assertEqual(res_admin_rev.status_code, 200)
        self.assertIn("doanh thu", res_admin_rev.json()["reply"].lower())

        # 3. Staff asking for debt -> ALLOWED
        res_debt = self.c.post("/api/ai/chat", json={"message": "Cho tôi danh sách ai đang nợ"}, headers=self.staff)
        self.assertEqual(res_debt.status_code, 200)
        self.assertIn("nợ", res_debt.json()["reply"].lower())

        # 4. Staff asking for patient info -> ALLOWED
        res_pat = self.c.post("/api/ai/chat", json={"message": "thông tin bệnh nhân Lan"}, headers=self.staff)
        self.assertEqual(res_pat.status_code, 200)
        self.assertTrue(len(res_pat.json()["reply"]) > 20)

    def test_72_admin_gemini_api_key_setting(self):
        # Admin gets current settings
        res = self.c.get("/api/admin/settings", headers=self.admin)
        self.assertEqual(res.status_code, 200)
        current = res.json()

        # Update with gemini_api_key
        current["gemini_api_key"] = "AIzaSyTestMockKey12345"
        update_res = self.c.post("/api/admin/settings", json=current, headers=self.admin)
        self.assertEqual(update_res.status_code, 200)

        # Verify saved
        res2 = self.c.get("/api/admin/settings", headers=self.admin)
        self.assertEqual(res2.json()["gemini_api_key"], "AIzaSyTestMockKey12345")

    def test_73_ai_search_all_historical_patients(self):
        # Create an older patient with treatment in isolated test db
        p_old = self.new_patient(name="Đỗ Đình Huy", phone="0862898462", yob=1988, address="Cửu Yên", branch="Cửu Yên")
        p_old_id = p_old["id"]
        self.c.post("/api/treatments", headers=self.staff, json={
            "patient_id": p_old_id, "procedure": "Làm cầu răng sứ", "tooth": "R24", "cost": 3000000, "dentist": "Thắng"
        })

        # 1. Search older patient by name with conversational prefix "bệnh nhân cũ"
        res_old = self.c.post("/api/ai/chat", json={"message": "tra cứu bệnh nhân cũ Đỗ Đình Huy"}, headers=self.staff)
        self.assertEqual(res_old.status_code, 200)
        reply = res_old.json()["reply"]
        self.assertTrue("ĐỖ ĐÌNH HUY" in reply or "Đỗ Đình Huy" in reply or "cầu răng sứ" in reply.lower())

        # 2. Search by given name with prefix "anh"
        res_huy = self.c.post("/api/ai/chat", json={"message": "hồ sơ của anh Huy"}, headers=self.staff)
        self.assertEqual(res_huy.status_code, 200)
        self.assertTrue("huy" in res_huy.json()["reply"].lower())

        # 3. Search by phone number
        res_phone = self.c.post("/api/ai/chat", json={"message": "tìm số 0862898462"}, headers=self.staff)
        self.assertEqual(res_phone.status_code, 200)
        self.assertTrue("0862898462" in res_phone.json()["reply"] or "Đỗ Đình Huy" in res_phone.json()["reply"])

    def test_99_logout_invalidates_token(self):
        h = self.login("letan", "abc123")
        self.assertEqual(self.c.post("/api/auth/logout", headers=h).status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me", headers=h).status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)

import unittest
import re
import unicodedata
from app.textutil import clean, strip_accents

def has_vietnamese_accent(text: str) -> bool:
    norm = clean(text)
    stripped = strip_accents(norm)
    return norm.lower() != stripped.lower()

def supply_matches(query: str, item: dict) -> bool:
    if not query or not query.strip():
        return True
    q = query.strip()
    name_code_raw = f"{item.get('name') or ''} {item.get('code') or ''}"
    supplier_raw = item.get("supplier") or ""
    
    name_code_norm = strip_accents(name_code_raw).lower()
    supplier_norm = strip_accents(supplier_raw).lower()

    if has_vietnamese_accent(q):
        q_tokens = q.lower().split()
        for tok in q_tokens:
            pattern_accent = r'(?<!\w)' + re.escape(tok) + r'(?!\w)'
            in_name = bool(re.search(pattern_accent, name_code_raw.lower()))
            if not in_name:
                if len(tok) >= 4 and tok in supplier_raw.lower():
                    continue
                return False
        return True
    else:
        q_tokens = strip_accents(q).lower().split()
        for tok in q_tokens:
            if len(tok) <= 2:
                pattern = r'(?<![a-z0-9])' + re.escape(tok) + r'(?![a-z0-9])'
                if not re.search(pattern, name_code_norm):
                    return False
            else:
                full_norm = f"{name_code_norm} {supplier_norm}"
                if tok not in full_norm:
                    return False
        return True

class TestSupplySearch(unittest.TestCase):
    def setUp(self):
        self.items = [
            {"name": "Thuốc tê Pháp Septanest 4% SPAD", "code": "VT00001", "supplier": "Septodont", "category": "Thuốc & Tê"},
            {"name": "Xi măng gắn trám răng Fuji 1 (GC Nhật Bản)", "code": "VT00002", "supplier": "Cty Thiết bị Y tế Hà Nội", "category": "Trám răng & Điều trị"},
            {"name": "Gel tê bề mặt niêm mạc Dolo Gel", "code": "VT00003", "supplier": "Dolo", "category": "Thuốc & Tê"},
            {"name": "Găng tay y tế có bột Vglove Size M", "code": "VT00004", "supplier": "Vglove", "category": "Vật liệu tiêu hao"},
            {"name": "Composite trám răng thẩm mỹ 3M Z250", "code": "VT00005", "supplier": "3M ESPE", "category": "Trám răng & Điều trị"},
        ]

    def test_search_te_accented(self):
        matched = [it["name"] for it in self.items if supply_matches("tê", it)]
        self.assertEqual(matched, [
            "Thuốc tê Pháp Septanest 4% SPAD",
            "Gel tê bề mặt niêm mạc Dolo Gel",
        ])

    def test_search_te_unaccented(self):
        matched = [it["name"] for it in self.items if supply_matches("te", it)]
        self.assertIn("Thuốc tê Pháp Septanest 4% SPAD", matched)
        self.assertIn("Gel tê bề mặt niêm mạc Dolo Gel", matched)
        self.assertNotIn("Composite trám răng thẩm mỹ 3M Z250", matched)
        self.assertNotIn("Xi măng gắn trám răng Fuji 1 (GC Nhật Bản)", matched)

    def test_search_composite(self):
        matched = [it["name"] for it in self.items if supply_matches("comp", it)]
        self.assertEqual(matched, ["Composite trám răng thẩm mỹ 3M Z250"])

    def test_search_more_cases(self):
        self.assertEqual([it["name"] for it in self.items if supply_matches("trám", it)], [
            "Xi măng gắn trám răng Fuji 1 (GC Nhật Bản)",
            "Composite trám răng thẩm mỹ 3M Z250"
        ])
        self.assertEqual([it["name"] for it in self.items if supply_matches("fuji", it)], [
            "Xi măng gắn trám răng Fuji 1 (GC Nhật Bản)"
        ])
        self.assertEqual([it["name"] for it in self.items if supply_matches("3m", it)], [
            "Composite trám răng thẩm mỹ 3M Z250"
        ])
        self.assertEqual([it["name"] for it in self.items if supply_matches("septodont", it)], [
            "Thuốc tê Pháp Septanest 4% SPAD"
        ])
        self.assertEqual([it["name"] for it in self.items if supply_matches("vt00003", it)], [
            "Gel tê bề mặt niêm mạc Dolo Gel"
        ])

    def test_search_multi_word(self):
        matched = [it["name"] for it in self.items if supply_matches("thuoc te", it)]
        self.assertEqual(matched, ["Thuốc tê Pháp Septanest 4% SPAD"])

if __name__ == "__main__":
    unittest.main()

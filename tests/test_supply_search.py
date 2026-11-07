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
    raw_fields = [
        item.get("name") or "",
        item.get("code") or "",
        item.get("supplier") or "",
    ]
    full_raw = " ".join(raw_fields).lower()
    full_norm = " ".join(strip_accents(f) for f in raw_fields).lower()

    if has_vietnamese_accent(q):
        q_lower = q.lower()
        q_tokens = q_lower.split()
        return all(tok in full_raw for tok in q_tokens)
    else:
        q_tokens = strip_accents(q).lower().split()
        for tok in q_tokens:
            if len(tok) <= 2:
                pattern = r"(?<![a-z0-9])" + re.escape(tok)
                if not re.search(pattern, full_norm):
                    return False
            else:
                if tok not in full_norm:
                    return False
        return True

class TestSupplySearch(unittest.TestCase):
    def setUp(self):
        self.items = [
            {"name": "Thuốc tê Pháp Septanest", "code": "VT00001", "supplier": "Septodont", "category": "Thuốc & Tê"},
            {"name": "Gel tê Dolo Gel", "code": "VT00002", "supplier": "Dolo", "category": "Thuốc & Tê"},
            {"name": "Composite trám răng thẩm mỹ 3M Z250", "code": "VT00003", "supplier": "3M ESPE", "category": "Vật liệu tiêu hao"},
            {"name": "Chỉ phẫu thuật vicryl 4.0", "code": "VT00004", "supplier": "Ethicon", "category": "Dụng cụ & Vật tư phẫu thuật"},
        ]

    def test_search_te_accented(self):
        matched = [it["name"] for it in self.items if supply_matches("tê", it)]
        self.assertIn("Thuốc tê Pháp Septanest", matched)
        self.assertIn("Gel tê Dolo Gel", matched)
        self.assertNotIn("Composite trám răng thẩm mỹ 3M Z250", matched)
        self.assertNotIn("Chỉ phẫu thuật vicryl 4.0", matched)

    def test_search_te_unaccented(self):
        matched = [it["name"] for it in self.items if supply_matches("te", it)]
        self.assertIn("Thuốc tê Pháp Septanest", matched)
        self.assertIn("Gel tê Dolo Gel", matched)
        self.assertNotIn("Composite trám răng thẩm mỹ 3M Z250", matched)

    def test_search_composite(self):
        matched = [it["name"] for it in self.items if supply_matches("comp", it)]
        self.assertEqual(matched, ["Composite trám răng thẩm mỹ 3M Z250"])

    def test_search_more_cases(self):
        self.assertEqual([it["name"] for it in self.items if supply_matches("trám", it)], ["Composite trám răng thẩm mỹ 3M Z250"])
        self.assertEqual([it["name"] for it in self.items if supply_matches("tram", it)], ["Composite trám răng thẩm mỹ 3M Z250"])
        self.assertEqual([it["name"] for it in self.items if supply_matches("chỉ", it)], ["Chỉ phẫu thuật vicryl 4.0"])
        self.assertEqual([it["name"] for it in self.items if supply_matches("chi", it)], ["Chỉ phẫu thuật vicryl 4.0"])
        self.assertEqual([it["name"] for it in self.items if supply_matches("3m", it)], ["Composite trám răng thẩm mỹ 3M Z250"])
        self.assertEqual([it["name"] for it in self.items if supply_matches("septodont", it)], ["Thuốc tê Pháp Septanest"])
        self.assertEqual([it["name"] for it in self.items if supply_matches("vt00002", it)], ["Gel tê Dolo Gel"])

    def test_search_multi_word(self):
        matched = [it["name"] for it in self.items if supply_matches("thuoc te", it)]
        self.assertEqual(matched, ["Thuốc tê Pháp Septanest"])

if __name__ == "__main__":
    unittest.main()

"""Text helpers: accent-insensitive search, Vietnamese title-case, phone parsing."""
import re
import unicodedata

_WS = re.compile(r"\s+")
_PHONE10 = re.compile(r"(?<!\d)(0\d{9})(?!\d)")
_PHONE_LIKE = re.compile(r"[\d\s.\-+()]{6,}")


def clean(s) -> str:
    """NFC-normalise (Excel may store decomposed accents) and collapse whitespace; None -> ''."""
    if s is None:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFC", str(s))).strip()


def strip_accents(s) -> str:
    """Lowercase + remove Vietnamese diacritics, so 'Nguyễn Đức' matches 'nguyen duc'."""
    s = unicodedata.normalize("NFD", clean(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D").lower()


def vi_title(s) -> str:
    """'hoàng MẬU dũng' -> 'Hoàng Mậu Dũng'."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in clean(s).split(" ") if w)


def split_phone(raw):
    """Return (phone, note). Spreadsheet cells sometimes hold '0367589964 mẹ 0397667159'."""
    s = clean(raw)
    if not s:
        return "", ""
    nums = _PHONE10.findall(s)
    if not nums:
        merged = re.sub(r"(?<=\d)[ .\-](?=\d)", "", s)
        nums = _PHONE10.findall(merged)
    if nums:
        phone = nums[0]
        return phone, (s if s != phone else "")
    if _PHONE_LIKE.fullmatch(s):
        return s, ""
    return "", s

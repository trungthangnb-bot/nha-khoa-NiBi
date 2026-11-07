import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.excel_exporter import generate_nibi_excel_workbook

print("Generating Excel...")
bio = generate_nibi_excel_workbook()
dest = r"C:\QL NK NiBi\Quan_Ly_PK_NiBi_2026.xlsx"
os.makedirs(os.path.dirname(dest), exist_ok=True)
with open(dest, "wb") as f:
    f.write(bio.getvalue())
print(f"Exported Excel successfully to: {dest} ({os.path.getsize(dest)} bytes)")
sys.stdout.flush()
os._exit(0)

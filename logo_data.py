"""
logo_data.py - الشعار الرسمي لشركة تنمية الغذاء (Food Development Company)
المصدر: assets/fdc-logo.png (PNG بخلفية شفافة، مستخرج من الملف الرسمي)
"""
import base64
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fdc-logo.png")

with open(_PATH, "rb") as _f:
    LOGO_PNG = _f.read()

LOGO_BASE64 = "data:image/png;base64," + base64.b64encode(LOGO_PNG).decode("ascii")

"""اختبارات استيراد الفروع دفعة واحدة (branch_import.py) — لا تحتاج قاعدة بيانات"""

import io
import re

import pytest
from openpyxl import Workbook, load_workbook

import branch_import as bi

MAPS = "https://maps.app.goo.gl/abc123"
EXISTING = [
    {"id": 1, "branch_name": "Bawshar Branch", "branch_phone": "+96890000001", "location_url": MAPS,
     "city": "مسقط", "aliases": "بوشر"},
    {"id": 2, "branch_name": "Salalah Branch", "branch_phone": "", "location_url": "", "city": "ظفار", "aliases": ""},
]


def make_file(rows, header=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Branches"
    ws.append(header or [title for _, title, _ in bi.COLUMNS])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def preview(rows, existing=EXISTING, header=None):
    return bi.build_preview(bi.read_rows(make_file(rows, header)), existing)


def by_name(result, name):
    return next(i for i in result["items"] if i["branch_name"] == name)


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((re.sub(r"\s+", " ", sql).strip(), params))


# ----------------------------- القالب -----------------------------

def test_template_is_prefilled_with_current_branches():
    wb = load_workbook(io.BytesIO(bi.build_template("Kucu", EXISTING)))
    assert wb.sheetnames == ["Branches", "Lists", "Instructions"]
    ws = wb["Branches"]
    assert [c.value for c in ws[1]] == [t for _, t, _ in bi.COLUMNS]
    assert ws["A2"].value == "Bawshar Branch"
    assert ws["E2"].value == "بوشر"
    assert ws["A3"].value == "Salalah Branch"
    assert ws["A4"].value is None
    assert wb["Lists"].sheet_state == "hidden"
    assert "Kucu" in wb["Instructions"]["A2"].value


def test_template_round_trip_is_all_unchanged():
    rows = bi.read_rows(bi.build_template("Kucu", EXISTING))
    result = bi.build_preview(rows, EXISTING)
    assert result["summary"] == {"new": 0, "updated": 0, "unchanged": 2, "error": 0}


def test_empty_template_for_new_customer():
    rows = bi.read_rows(bi.build_template("New Co", []))
    assert rows == []


# ----------------------------- المعاينة -----------------------------

def test_new_updated_unchanged():
    result = preview([
        ["Bawshar Branch", "مسقط", "+96890000001", MAPS, "بوشر"],          # بدون تغيير
        ["salalah branch", "ظفار", "23 456 789", "", "صلالة, Salalah / SLL"],  # تحديث (الاسم بحروف مختلفة)
        ["Al Khoudh Branch", "", "", "", ""],                                 # جديد
    ])
    assert result["summary"] == {"new": 1, "updated": 1, "unchanged": 1, "error": 0}

    upd = by_name(result, "salalah branch")
    assert upd["id"] == 2
    assert upd["changes"]["branch_phone"] == {"old": "", "new": "23 456 789"}
    assert upd["changes"]["aliases"]["new"] == "صلالة, Salalah, SLL"
    assert upd["changes"]["branch_name"] == {"old": "Salalah Branch", "new": "salalah branch"}

    new = by_name(result, "Al Khoudh Branch")
    assert new["status"] == "new"
    assert new["values"]["city"] == bi.DEFAULT_CITY  # المنطقة الافتراضية


def test_empty_optional_cell_keeps_current_value():
    result = preview([["Bawshar Branch", "", "", "", ""]])
    assert by_name(result, "Bawshar Branch")["status"] == "unchanged"


def test_errors():
    result = preview([
        ["", "مسقط", "+968 1234567", "", ""],          # اسم فارغ
        ["Seeb Branch", "", "abc", "", ""],             # هاتف غير صحيح
        ["Ruwi Branch", "", "", "https://example.com/x", ""],  # رابط ليس Google Maps
        ["Amerat Branch", "", "", "", ""],
        ["amerat  branch", "", "", "", ""],             # مكرر في الملف
        ["Sohar Branch", "", "", "", "Bawshar Branch"],  # الاسم البديل هو اسم فرع آخر
    ])
    assert result["summary"]["error"] == 5
    assert result["summary"]["new"] == 1
    errors = {i["row"]: i["errors"] for i in result["items"] if i["status"] == "error"}
    assert "اسم الفرع فارغ" in errors[2]
    assert "رقم الهاتف غير صحيح" in errors[3]
    assert "رابط الموقع ليس رابط Google Maps" in errors[4]
    assert any("مكرر" in e for e in errors[6])
    assert any("اسم فرع آخر" in e for e in errors[7])


@pytest.mark.parametrize("url,ok", [
    ("https://maps.app.goo.gl/abc", True),
    ("https://goo.gl/maps/abc", True),
    ("https://www.google.com/maps/place/x", True),
    ("https://maps.google.com/?q=23.5,58.4", True),
    ("http://google.com.om/maps", True),
    ("https://evil.com/google.com/maps", False),
    ("https://google.com.evil.com/maps", False),
    ("maps.app.goo.gl/abc", False),
])
def test_maps_url(url, ok):
    assert bi.valid_maps_url(url) is ok


def test_phone_read_as_number_from_excel():
    result = preview([["Nizwa Branch", "", 96890000002, "", ""]], existing=[])
    assert by_name(result, "Nizwa Branch")["values"]["branch_phone"] == "96890000002"


def test_columns_found_by_header_even_if_reordered():
    header = ["الأسماء البديلة", "اسم الفرع", "هاتف الفرع"]
    result = preview([["SLL", "Salalah Branch", "+96823000000"]], header=header)
    item = by_name(result, "Salalah Branch")
    assert item["status"] == "updated"
    assert item["changes"]["aliases"]["new"] == "SLL"


def test_missing_name_column_is_rejected():
    with pytest.raises(ValueError):
        bi.read_rows(make_file([["x"]], header=["Something else"]))


def test_not_an_excel_file():
    with pytest.raises(ValueError):
        bi.read_rows(b"not a spreadsheet")


def test_blank_rows_are_ignored():
    result = preview([[None, None, None, None, None], ["Ibri Branch", "الظاهرة", "", "", ""]], existing=[])
    assert result["summary"]["new"] == 1
    assert len(result["items"]) == 1


# ----------------------------- الحفظ -----------------------------

def test_apply_inserts_and_updates_only_and_never_deletes():
    result = preview([
        ["Salalah Branch", "", "+96823000000", "", ""],
        ["Al Khoudh Branch", "", "", "", "الخوض"],
        ["", "", "", "", ""],
        ["Bad", "", "xx", "", ""],
    ])
    cur = FakeCursor()
    out = bi.apply_preview(cur, 7, result)
    assert out == {"added": 1, "updated": 1, "skipped": 1}
    sqls = [s for s, _ in cur.calls]
    assert not any("DELETE" in s for s in sqls)
    insert = next(c for c in cur.calls if c[0].startswith("INSERT"))
    assert insert[1] == (7, "Al Khoudh Branch", "", "", bi.DEFAULT_CITY, "الخوض")
    update = next(c for c in cur.calls if c[0].startswith("UPDATE"))
    assert update[0] == "UPDATE customer_branches SET branch_phone = %s WHERE id = %s AND customer_id = %s;"
    assert update[1] == ["+96823000000", 2, 7]


# ----------------------------- الأسماء البديلة في البوت -----------------------------

def _norm(s):
    # نفس دالة التطبيع في order_engine._norm
    s = (s or "").lower()
    s = re.sub(r"\b(branch|br)\b|فرع", " ", s)
    return re.sub(r"[^\w؀-ۿ]+", "", s)


def test_match_alias():
    aliases = {"Salalah Branch": "صلالة, Salalah, SLL", "Bawshar Branch": "بوشر"}
    assert bi.match_alias(_norm, "SLL", aliases) == "Salalah Branch"
    assert bi.match_alias(_norm, "فرع صلالة", aliases) == "Salalah Branch"
    assert bi.match_alias(_norm, "بوشر", aliases) == "Bawshar Branch"
    assert bi.match_alias(_norm, "Sohar", aliases) is None
    assert bi.match_alias(_norm, "", aliases) is None
    assert bi.match_alias(_norm, "SLL", None) is None


def test_split_aliases():
    assert bi.split_aliases("صلالة، Salalah / SLL; salalah\nسلالة") == ["صلالة", "Salalah", "SLL", "سلالة"]
    assert bi.split_aliases(None) == []

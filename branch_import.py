"""
branch_import.py - استيراد فروع العميل دفعة واحدة عبر قالب Excel

- القالب يُنزَّل معبأً مسبقاً بفروع العميل الحالية، مع ورقة تعليمات بلغتين.
- عند الرفع: معاينة قبل الحفظ (جديد / محدّث / بدون تغيير / خطأ)، والمطابقة باسم الفرع.
- لا حذف تلقائي أبداً: الفروع غير الموجودة في الملف تبقى كما هي.
- الخلية الاختيارية الفارغة في فرع موجود لا تمسح القيمة الحالية.
- عمود "الأسماء البديلة" يساعد البوت على مطابقة الفرع (صلالة / Salalah / SLL) دون أن يسأل العميل.

هذا الملف لا يعتمد على قاعدة البيانات مباشرة (يستقبل cursor من الخارج) حتى يمكن اختباره منفصلاً.
"""

import io
import re
from typing import Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

MAX_ROWS = 2000
DEFAULT_CITY = "مسقط"

# محافظات سلطنة عُمان (قائمة منسدلة في القالب، مع السماح بكتابة مدينة أخرى)
OMAN_REGIONS = [
    "مسقط", "ظفار", "مسندم", "البريمي", "الداخلية", "شمال الباطنة", "جنوب الباطنة",
    "جنوب الشرقية", "شمال الشرقية", "الظاهرة", "الوسطى",
]

# الأعمدة: (المفتاح، عنوان العمود في القالب، كلمات تُعرف بها عند القراءة)
COLUMNS = [
    ("branch_name", "Branch name * / اسم الفرع", ("branch name", "اسم الفرع")),
    ("city", "City / Region - المدينة / المنطقة", ("city", "region", "المدينة", "المنطقة")),
    ("branch_phone", "Branch phone - هاتف الفرع", ("phone", "هاتف")),
    ("location_url", "Google Maps link - رابط الموقع", ("maps", "map", "location", "رابط", "الموقع")),
    ("aliases", "Alternate names - الأسماء البديلة", ("alternate", "alias", "البديلة", "بديلة")),
]

_ALIAS_SPLIT = re.compile(r"[,،;؛/|\n]+")
# نطاق Google فقط (google.com / google.com.om / google.om / goo.gl) — وليس google.com.evil.com
_MAPS_HOST = re.compile(r"^https?://([a-z0-9-]+\.)*(google\.(com(\.[a-z]{2})?|[a-z]{2})|goo\.gl)(/|\?|$)", re.I)


# ----------------------------- أدوات مساعدة -----------------------------

def name_key(name: str) -> str:
    """مفتاح مطابقة اسم الفرع: بدون فرق بين الحروف الكبيرة والصغيرة أو المسافات الزائدة"""
    return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


def split_aliases(text) -> list:
    """تقسيم الأسماء البديلة (فاصلة، فاصلة عربية، شرطة مائلة، سطر جديد) مع حذف المكرر"""
    out, seen = [], set()
    for part in _ALIAS_SPLIT.split(str(text or "")):
        part = re.sub(r"\s+", " ", part).strip()
        if part and name_key(part) not in seen:
            seen.add(name_key(part))
            out.append(part)
    return out


def join_aliases(aliases: list) -> str:
    return ", ".join(aliases)


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # أرقام الهواتف قد تُقرأ كأرقام عشرية من Excel
    return str(value).strip()


def valid_phone(phone: str) -> bool:
    if not re.fullmatch(r"[+\d\s\-()]+", phone):
        return False
    return len(re.sub(r"\D", "", phone)) >= 6


def valid_maps_url(url: str) -> bool:
    return bool(_MAPS_HOST.match(url.strip()))


# ----------------------------- القالب -----------------------------

def build_template(customer_name: str, branches: list) -> bytes:
    """قالب Excel معبأ مسبقاً بفروع العميل الحالية + ورقة تعليمات بلغتين"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Branches"
    ws.sheet_view.rightToLeft = False

    header_fill = PatternFill("solid", fgColor="3A056A")
    for col, (_, title, _) in enumerate(COLUMNS, start=1):
        c = ws.cell(row=1, column=col, value=title)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    for letter, width in zip("ABCDE", (32, 22, 20, 44, 36)):
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"

    for r, b in enumerate(branches, start=2):
        ws.cell(row=r, column=1, value=b.get("branch_name") or "")
        ws.cell(row=r, column=2, value=b.get("city") or "")
        ws.cell(row=r, column=3, value=b.get("branch_phone") or "").number_format = "@"
        ws.cell(row=r, column=4, value=b.get("location_url") or "")
        ws.cell(row=r, column=5, value=b.get("aliases") or "")
    for r in range(len(branches) + 2, MAX_ROWS + 2):
        ws.cell(row=r, column=3).number_format = "@"  # الهاتف نص حتى لا يضيع الصفر أو علامة +

    # قائمة المناطق في ورقة مخفية؛ تحذير فقط حتى يمكن كتابة مدينة غير موجودة في القائمة
    lists = wb.create_sheet("Lists")
    for i, region in enumerate(OMAN_REGIONS, start=1):
        lists.cell(row=i, column=1, value=region)
    lists.sheet_state = "hidden"
    dv = DataValidation(type="list", formula1=f"=Lists!$A$1:$A${len(OMAN_REGIONS)}", allow_blank=True)
    dv.errorStyle = "warning"
    dv.error = "Not in the list - you can still keep it. / ليست في القائمة، يمكنك الإبقاء عليها."
    ws.add_data_validation(dv)
    dv.add(f"B2:B{MAX_ROWS + 1}")

    info = wb.create_sheet("Instructions")
    info.column_dimensions["A"].width = 70
    info.column_dimensions["B"].width = 70
    rows = [
        ("Bulk branch import", "استيراد الفروع دفعة واحدة"),
        (f"Customer: {customer_name}", f"العميل: {customer_name}"),
        ("", ""),
        ("1. Fill the 'Branches' sheet: one branch per row.", "1. عبّئ ورقة 'Branches': فرع واحد في كل صف."),
        ("2. Branch name is required. Other columns are optional.", "2. اسم الفرع إلزامي، وبقية الأعمدة اختيارية."),
        ("3. Existing branches are matched by name. Changed values are updated.",
         "3. الفروع الموجودة تُطابق بالاسم، والقيم المتغيرة تُحدَّث."),
        ("4. Leaving an optional cell empty keeps the current value.",
         "4. ترك خلية اختيارية فارغة يُبقي القيمة الحالية كما هي."),
        ("5. Nothing is deleted: branches missing from the file stay as they are.",
         "5. لا يُحذف شيء: الفروع غير الموجودة في الملف تبقى كما هي."),
        ("6. Alternate names: other ways the customer writes the branch, separated by commas "
         "(e.g. صلالة, Salalah, SLL). The bot uses them to recognise the branch.",
         "6. الأسماء البديلة: طرق أخرى يكتب بها العميل اسم الفرع، مفصولة بفواصل "
         "(مثل: صلالة, Salalah, SLL). يستخدمها البوت للتعرف على الفرع."),
        ("7. Google Maps link must be a Google Maps URL (maps.app.goo.gl or google.com/maps).",
         "7. رابط الموقع يجب أن يكون رابط Google Maps (maps.app.goo.gl أو google.com/maps)."),
        ("8. Upload the file back from the dashboard. You will see a preview before saving.",
         "8. ارفع الملف من لوحة التحكم، وستظهر معاينة قبل الحفظ."),
    ]
    for r, (en, ar) in enumerate(rows, start=1):
        info.cell(row=r, column=1, value=en).alignment = Alignment(wrap_text=True, vertical="top")
        c = info.cell(row=r, column=2, value=ar)
        c.alignment = Alignment(wrap_text=True, vertical="top", horizontal="right")
    info["A1"].font = Font(bold=True, size=14)
    info["B1"].font = Font(bold=True, size=14)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------- قراءة الملف والمعاينة -----------------------------

def _map_columns(header: list) -> dict:
    """تحديد مكان كل عمود من عنوانه (حتى لو أعاد المستخدم ترتيب الأعمدة)"""
    mapping = {}
    for idx, raw in enumerate(header):
        title = name_key(raw)
        if not title:
            continue
        for key, _, words in COLUMNS:
            if key not in mapping and any(w in title for w in words):
                mapping[key] = idx
                break
    return mapping


def read_rows(data: bytes) -> list:
    """قراءة صفوف ورقة الفروع. يرفع ValueError برسالة عربية واضحة عند مشكلة في الملف"""
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValueError("تعذر قراءة الملف. تأكد أنه ملف Excel (.xlsx) من القالب.")
    ws = wb["Branches"] if "Branches" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("الملف فارغ.")
    mapping = _map_columns(list(rows[0]))
    if "branch_name" not in mapping:
        raise ValueError("لم يُعثر على عمود 'اسم الفرع / Branch name'. استخدم القالب المنزَّل من المنصة.")
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f"الحد الأقصى {MAX_ROWS} فرع في الملف الواحد.")

    out = []
    for excel_row, values in enumerate(rows[1:], start=2):
        values = list(values or [])
        rec = {"row": excel_row}
        for key, _, _ in COLUMNS:
            idx = mapping.get(key)
            rec[key] = _cell_text(values[idx]) if idx is not None and idx < len(values) else ""
        if any(rec[k] for k, _, _ in COLUMNS):
            out.append(rec)
    return out


def build_preview(rows: list, existing: list) -> dict:
    """
    مقارنة صفوف الملف مع الفروع الحالية للعميل.
    كل صف يأخذ حالة: new / updated / unchanged / error، مع قائمة التغييرات أو الأخطاء.
    """
    by_name = {name_key(b.get("branch_name")): b for b in existing}
    seen = {}
    items = []

    for rec in rows:
        errors = []
        name = re.sub(r"\s+", " ", rec["branch_name"]).strip()
        if not name:
            errors.append("اسم الفرع فارغ")
        elif len(name) > 150:
            errors.append("اسم الفرع أطول من 150 حرفاً")
        key = name_key(name)
        if key and key in seen:
            errors.append(f"اسم مكرر في الملف (الصف {seen[key]})")
        if rec["branch_phone"] and not valid_phone(rec["branch_phone"]):
            errors.append("رقم الهاتف غير صحيح")
        if rec["location_url"] and not valid_maps_url(rec["location_url"]):
            errors.append("رابط الموقع ليس رابط Google Maps")
        if key and key not in seen:
            seen[key] = rec["row"]

        item = {"row": rec["row"], "branch_name": name, "errors": errors, "changes": {}}
        if errors:
            item["status"] = "error"
            items.append(item)
            continue

        current = by_name.get(key)
        new_vals = {
            "city": rec["city"],
            "branch_phone": rec["branch_phone"],
            "location_url": rec["location_url"].strip(),
            "aliases": join_aliases(split_aliases(rec["aliases"])),
        }
        if current is None:
            item["status"] = "new"
            item["values"] = {**new_vals, "city": new_vals["city"] or DEFAULT_CITY}
        else:
            item["id"] = current.get("id")
            # الخلية الفارغة لا تمسح القيمة الحالية
            for field, val in new_vals.items():
                old = (current.get(field) or "").strip()
                if val and val != old:
                    item["changes"][field] = {"old": old, "new": val}
            if name != current.get("branch_name"):
                item["changes"]["branch_name"] = {"old": current.get("branch_name"), "new": name}
            item["status"] = "updated" if item["changes"] else "unchanged"
        items.append(item)

    # الاسم البديل لا يجوز أن يكون اسماً لفرع آخر
    final_names = {name_key(b.get("branch_name")): b.get("branch_name") for b in existing}
    for it in items:
        if it["status"] in ("new", "updated", "unchanged"):
            final_names[name_key(it["branch_name"])] = it["branch_name"]
    for it in items:
        if it["status"] == "error":
            continue
        aliases_text = (it.get("values") or {}).get("aliases") or (it["changes"].get("aliases") or {}).get("new") or ""
        for alias in split_aliases(aliases_text):
            other = final_names.get(name_key(alias))
            if other and name_key(other) != name_key(it["branch_name"]):
                it["errors"].append(f"الاسم البديل '{alias}' هو اسم فرع آخر ({other})")
        if it["errors"]:
            it["status"] = "error"

    summary = {s: sum(1 for i in items if i["status"] == s) for s in ("new", "updated", "unchanged", "error")}
    return {"items": items, "summary": summary}


# ----------------------------- الحفظ -----------------------------

def apply_preview(cur, customer_id: int, preview: dict) -> dict:
    """حفظ الصفوف الجديدة والمحدّثة فقط؛ صفوف الأخطاء تُتجاهل ولا يُحذف أي فرع"""
    added = updated = 0
    for it in preview["items"]:
        if it["status"] == "new":
            v = it["values"]
            cur.execute("""
                INSERT INTO customer_branches (customer_id, branch_name, branch_phone, location_url, city, aliases)
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (customer_id, it["branch_name"], v["branch_phone"], v["location_url"], v["city"], v["aliases"]))
            added += 1
        elif it["status"] == "updated":
            ch = it["changes"]
            sets, params = [], []
            for field in ("branch_name", "city", "branch_phone", "location_url", "aliases"):
                if field in ch:
                    sets.append(f"{field} = %s")
                    params.append(ch[field]["new"])
            params += [it["id"], customer_id]
            cur.execute(f"UPDATE customer_branches SET {', '.join(sets)} WHERE id = %s AND customer_id = %s;", params)
            updated += 1
    return {"added": added, "updated": updated, "skipped": preview["summary"]["error"]}


# ----------------------------- مطابقة البوت -----------------------------

def match_alias(key_fn, name: str, alias_map: Optional[dict]) -> Optional[str]:
    """
    مطابقة اسم فرع كتبه العميل مع الأسماء البديلة المسجلة.
    alias_map: {اسم الفرع الرسمي: "اسم1, اسم2"}؛ key_fn هي دالة التطبيع المستخدمة في محرك الطلبات.
    """
    key = key_fn(name)
    if not key or not alias_map:
        return None
    for branch, aliases in alias_map.items():
        for alias in split_aliases(aliases):
            if key_fn(alias) == key:
                return branch
    return None

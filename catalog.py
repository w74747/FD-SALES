"""
catalog.py - كتالوج المنتجات ومواصفات التعبئة وتحويل الوحدات

- استيراد ملف "Assortment" (Excel) كما هو: اكتشاف صف العناوين تلقائياً، وتعبئة الفئة/المجموعة
  للخلايا المدمجة، وتحديث المنتج بكوده (Product Code) إن وُجد.
- تحويل أي كمية (كرتون / كيس / حبة) إلى وحدة موحدة: عدد الحبات والوزن بالكيلوجرام،
  حتى يمكن جمع طلبيات العملاء الذين يطلبون بالكرتون مع من يطلبون بالكيس في التقارير.
"""

import io
import logging
import re
from typing import Optional

logger = logging.getLogger("SalesCRM.catalog")

SCHEMA = [
    "ALTER TABLE products_catalog ALTER COLUMN name_ar DROP NOT NULL;",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS category VARCHAR(120) DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS product_range VARCHAR(200) DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS technology VARCHAR(200) DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS piece_weight_g NUMERIC(10, 2);",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS specs_cm VARCHAR(120) DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS pieces_per_pack NUMERIC(10, 2);",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS pack_weight_g NUMERIC(10, 2);",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS packs_per_box NUMERIC(10, 2);",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS pieces_per_box NUMERIC(10, 2);",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS boxes_per_pallet NUMERIC(10, 2);",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS case_dimensions VARCHAR(60) DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS gross_box_kg NUMERIC(10, 3);",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS shelf_life_frozen VARCHAR(60) DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS shelf_life_secondary VARCHAR(60) DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS preparation TEXT DEFAULT '';",
    "ALTER TABLE products_catalog ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
    "ALTER TABLE order_lines ADD COLUMN IF NOT EXISTS product_sku VARCHAR(100);",
]

# عناوين أعمدة ملف Assortment ← اسم الحقل (البحث بجزء من العنوان، بغض النظر عن حالة الأحرف)
HEADER_MAP = [
    ("category", "category"),
    ("range", "product_range"),
    ("technology", "technology"),
    ("product code", "sku"),
    ("weight / piece", "piece_weight_g"),
    ("specs", "specs_cm"),
    ("pieces / pack", "pieces_per_pack"),
    ("pack weight", "pack_weight_g"),
    ("packs / box", "packs_per_box"),
    ("pieces / box", "pieces_per_box"),
    ("pallet", "boxes_per_pallet"),
    ("case dimensions", "case_dimensions"),
    ("gross weight", "gross_box_kg"),
    ("secondary shelf", "shelf_life_secondary"),
    ("shelf life", "shelf_life_frozen"),
    ("thawing", "preparation"),
    ("baking", "preparation"),
]
NUMERIC_FIELDS = {"piece_weight_g", "pieces_per_pack", "pack_weight_g", "packs_per_box", "pieces_per_box",
                  "boxes_per_pallet", "gross_box_kg"}
CARRY_FORWARD = ("category", "product_range", "technology")


def init_schema(run_ddl):
    for stmt in SCHEMA:
        run_ddl(stmt)


def _num(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = str(v).strip().replace(",", ".")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        f = float(s)
        return f if f > 0 else None
    return None


def _clean(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def parse_assortment(raw: bytes) -> dict:
    """
    قراءة ملف المنتجات. يعيد:
    {products: [...], incomplete: [...], inconsistent: [...], sheet: name}
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        header_idx = next((i for i, r in enumerate(rows[:40])
                           if any("product code" in _clean(c).lower() for c in r)), None)
        if header_idx is None:
            continue
        header = [_clean(c).lower() for c in rows[header_idx]]
        cols = {}
        for i, h in enumerate(header):
            for key, field in HEADER_MAP:
                if key in h and field not in cols:
                    if field == "shelf_life_frozen" and "secondary" in h:
                        continue
                    cols[field] = i
                    break
        if "sku" not in cols:
            continue
        # اسم المنتج: أول عمود بعد الكود بلا عنوان أو بعنوان name/description
        name_col = next((i for i in range(cols["sku"] + 1, len(header))
                         if header[i] == "" or "name" in header[i] or "description" in header[i]), cols["sku"] + 1)

        products, carry = [], {f: "" for f in CARRY_FORWARD}
        for r_i, r in enumerate(rows[header_idx + 1:], start=header_idx + 2):
            get = lambda f: r[cols[f]] if f in cols and cols[f] < len(r) else None
            for f in CARRY_FORWARD:
                if _clean(get(f)):
                    carry[f] = _clean(get(f)).split("  ")[0][:200]
            sku = _clean(get("sku"))
            name = _clean(r[name_col]) if name_col < len(r) else ""
            if not sku and not name:
                continue
            p = {"sku": sku or None, "name_en": name[:250], "row": r_i, **carry}
            for f in cols:
                if f in ("sku",) + CARRY_FORWARD:
                    continue
                p[f] = _num(get(f)) if f in NUMERIC_FIELDS else _clean(get(f))[:500]
            products.append(p)

        incomplete, inconsistent = [], []
        for p in products:
            ppp, ppb, pcs_box = p.get("pieces_per_pack"), p.get("packs_per_box"), p.get("pieces_per_box")
            if ppp and ppb and pcs_box and abs(ppp * ppb - pcs_box) > 0.01:
                inconsistent.append({"sku": p["sku"], "name": p["name_en"], "row": p["row"],
                                     "detail": f"{ppp:g} × {ppb:g} ≠ {pcs_box:g}"})
            if not pcs_box and ppp and ppb:
                p["pieces_per_box"] = ppp * ppb
            if not (p.get("pieces_per_pack") and p.get("pieces_per_box")) or not p.get("piece_weight_g"):
                missing = [lbl for f, lbl in (("piece_weight_g", "وزن الحبة"), ("pieces_per_pack", "حبات/كيس"),
                                              ("packs_per_box", "أكياس/كرتون")) if not p.get(f)]
                incomplete.append({"sku": p["sku"], "name": p["name_en"], "row": p["row"], "missing": missing})
        return {"sheet": ws.title, "products": products, "incomplete": incomplete, "inconsistent": inconsistent}
    raise ValueError("لم يُعثر على صف عناوين يحتوي 'Product Code' في الملف")


def upsert_products(cur, products: list) -> dict:
    created = updated = 0
    fields = ["name_en", "category", "product_range", "technology", "piece_weight_g", "specs_cm", "pieces_per_pack",
              "pack_weight_g", "packs_per_box", "pieces_per_box", "boxes_per_pallet", "case_dimensions",
              "gross_box_kg", "shelf_life_frozen", "shelf_life_secondary", "preparation"]
    for p in products:
        if not p.get("sku"):
            continue
        vals = [p.get(f) if p.get(f) not in ("",) else None for f in fields]
        weight_spec = f"{p['piece_weight_g']:g} g" if p.get("piece_weight_g") else ""
        pack_spec = f"{p['pieces_per_pack']:g} pcs/pack" if p.get("pieces_per_pack") else ""
        carton_spec = " · ".join(x for x in (
            f"{p['packs_per_box']:g} packs/box" if p.get("packs_per_box") else "",
            f"{p['pieces_per_box']:g} pcs/box" if p.get("pieces_per_box") else "") if x)
        cur.execute(f"""
            INSERT INTO products_catalog (sku, name_ar, {", ".join(fields)}, weight_spec, primary_packaging, carton_pack_spec, updated_at)
            VALUES (%s, %s, {", ".join(["%s"] * len(fields))}, %s, %s, %s, NOW())
            ON CONFLICT (sku) DO UPDATE SET {", ".join(f"{f} = EXCLUDED.{f}" for f in fields)},
                weight_spec = EXCLUDED.weight_spec, primary_packaging = EXCLUDED.primary_packaging,
                carton_pack_spec = EXCLUDED.carton_pack_spec, updated_at = NOW()
            RETURNING (xmax = 0) AS inserted;
        """, [p["sku"], p.get("name_en") or p["sku"]] + vals + [weight_spec, pack_spec, carton_spec])
        if cur.fetchone()["inserted"]:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


EXPORT_COLUMNS = [
    ("sku", "Product Code"), ("name_en", "Product Name"), ("name_ar", "Arabic Name"), ("category", "Category"),
    ("product_range", "Range"), ("technology", "Technology"), ("piece_weight_g", "Weight / Piece (g)"),
    ("specs_cm", "Specs (cm)"), ("pieces_per_pack", "Pieces / Pack"), ("pack_weight_g", "Pack Weight (g)"),
    ("packs_per_box", "Packs / Box"), ("pieces_per_box", "Pieces / Box"), ("boxes_per_pallet", "Boxes / Euro Pallet"),
    ("case_dimensions", "Case dimensions (cm)"), ("gross_box_kg", "Gross Weight per box (kg)"),
    ("shelf_life_frozen", "Shelf life (-18C)"), ("shelf_life_secondary", "Secondary shelf life (+4C)"),
    ("preparation", "Thawing / Baking"), ("notes", "Notes"),
]


def export_xlsx(rows: list) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append([lbl for _, lbl in EXPORT_COLUMNS])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="3A056A")
    for r in rows:
        ws.append([float(r[f]) if isinstance(r.get(f), (int, float)) or (r.get(f) is not None and f in NUMERIC_FIELDS)
                   else (r.get(f) or "") for f, _ in EXPORT_COLUMNS])
    widths = {"A": 14, "B": 38, "C": 24, "D": 20}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ======================= تحويل الوحدات =======================

UNIT_ALIASES = {
    "box": ("box", "boxes", "carton", "cartons", "cartoon", "ctn", "ctns", "case", "cases", "كرتون", "كراتين", "كرتونة", "صندوق"),
    "pack": ("pack", "packs", "packet", "packets", "pkt", "pkts", "bag", "bags", "كيس", "اكياس", "أكياس", "باكيت", "علبة"),
    "piece": ("piece", "pieces", "pcs", "pc", "pce", "unit", "units", "حبة", "حبات", "قطعة", "قطع"),
}


def normalize_unit(unit: str) -> Optional[str]:
    u = (unit or "").strip().lower().rstrip(".")
    for norm, names in UNIT_ALIASES.items():
        if u in names:
            return norm
    return None


def pieces_per_unit(product: dict, unit: str) -> Optional[float]:
    """عدد الحبات في وحدة واحدة من الوحدة المطلوبة (كرتون/كيس/حبة) حسب مواصفات المنتج"""
    norm = normalize_unit(unit)
    if not product or not norm:
        return None
    if norm == "piece":
        return 1.0
    ppp = float(product["pieces_per_pack"]) if product.get("pieces_per_pack") else None
    if norm == "pack":
        return ppp
    if product.get("pieces_per_box"):
        return float(product["pieces_per_box"])
    if ppp and product.get("packs_per_box"):
        return ppp * float(product["packs_per_box"])
    return None


def line_metrics(product: Optional[dict], quantity: float, unit: str) -> dict:
    """الكمية بالحبات والكيلوجرام (None إذا كانت مواصفات المنتج ناقصة)"""
    ppu = pieces_per_unit(product, unit) if product else None
    if ppu is None:
        return {"pieces": None, "weight_kg": None}
    pieces = float(quantity) * ppu
    weight = pieces * float(product["piece_weight_g"]) / 1000.0 if product.get("piece_weight_g") else None
    return {"pieces": pieces, "weight_kg": weight}


def get_by_sku(cur, sku: Optional[str]) -> Optional[dict]:
    if not sku:
        return None
    cur.execute("SELECT * FROM products_catalog WHERE sku = %s;", (sku,))
    row = cur.fetchone()
    return dict(row) if row else None


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9؀-ۿ]+", "", (s or "").lower())


def find_sku(cur, product_name: str) -> Optional[str]:
    """مطابقة اسم المنتج مع الكتالوج: بالكود ثم بالاسم الكامل (فقط إذا كان الاسم فريداً)"""
    name = (product_name or "").strip()
    if not name:
        return None
    m = re.search(r"\[([A-Za-z0-9\-]+)\]\s*$", name)
    if m and get_by_sku(cur, m.group(1)):
        return m.group(1)
    cur.execute("SELECT sku FROM products_catalog WHERE sku = %s;", (name,))
    row = cur.fetchone()
    if row:
        return row["sku"]
    cur.execute("SELECT sku, name_en, name_ar FROM products_catalog;")
    key = _key(name)
    hits = [r["sku"] for r in cur.fetchall() if key and key in (_key(r["name_en"]), _key(r["name_ar"]))]
    return hits[0] if len(hits) == 1 else None

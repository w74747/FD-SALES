"""
order_engine.py - محرك فهم الطلبات الذكي لمجموعات واتساب العملاء

دورة العمل:
1. كل رسالة في مجموعة عميل تُحفظ (ذاكرة المحادثة) مع الرسالة المقتبسة.
2. رسالة العميل تُقرأ في سياق: آخر الرسائل (بما فيها سؤال المندوب)، ملف طلب العميل
   (أسماء أصنافه بلغته، فرعه ووحدته الافتراضية)، الأصناف المعتادة، وطلبيات الأيام القادمة.
3. النتيجة: طلب جديد / إضافة / تعديل / إلغاء / تأكيد / ليس طلباً.
4. "طلبية اليوم": لكل عميل وتاريخ تسليم طلبية واحدة تتراكم عليها الصورة والإضافات والتعديلات،
   وتستلم اللوجستيك ملخصاً موحداً ثم "تحديثاً" بالفروقات فقط.
5. الطلب الغامض: البوت يسأل العميل في المجموعة + تنبيه خاص للمندوب برابط اعتماد بضغطة.
   أول من يؤكد (العميل أو المندوب) يعتمد الطلب.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import ai_client
import branch_import
import catalog
import i18n
import knowledge
import media_tools
import security
from psycopg2.extras import Json

logger = logging.getLogger("SalesCRM.orders")

BUSINESS_TZ = timezone(timedelta(hours=int(os.getenv("BUSINESS_UTC_OFFSET", "4"))))  # مسقط UTC+4
AUTO_CONFIRM_THRESHOLD = float(os.getenv("ORDER_AUTO_CONFIRM_THRESHOLD", "0.8"))
CONTEXT_MESSAGES = 14
CONTEXT_HOURS = 72
PENDING_TTL_HOURS = 24
BOT_NAME = "FDC Bot"

ORDER_KINDS = ("new_order", "addition", "modification", "cancellation")
WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

AFFIRMATIVE_RE = re.compile(
    r"^\s*(ok+|okay|yes|yep|sure|confirm(ed)?|noted|done|approved?|correct|"
    r"تم|تمام|نعم|ايوه|أيوه|اي|إي|صح|صحيح|اوكي|أوكي|اوك|موافق|معتمد|اعتمد|مضبوط|بالضبط|👍|✅|👌)"
    r"[\s!.,👍✅🙏]*$",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(r"^\s*(no|nope|cancel|لا|لأ|كنسل|الغ|ألغ|خطأ|غلط)\b", re.IGNORECASE)
TRIVIAL_RE = re.compile(
    r"^[\s\W]*(السلام عليكم|سلام عليكم|وعليكم السلام|وعليكم السلام ورحمة الله( وبركاته)?|صباح الخير|صباح النور|مساء الخير|مساء النور|"
    r"شكرا|شكراً|مشكور|مشكورين|يعطيكم العافية|الله يعافيك|تمام|اوكي|ok|okay|thanks|thank you|thx|good morning|good evening|"
    r"hi|hello|welcome|noted|sure|👍|🙏|❤️|🌹)[\s\W]*$",
    re.IGNORECASE,
)
STAFF_ACTIVE_MINUTES = 10
ORDER_HINT_RE = re.compile(
    r"\d|order|box|ctn|carton|cartoon|packet|pkt|same|usual|repeat|"
    r"طلب|اوردر|أوردر|كرتون|كراتين|كيس|اكياس|نفس|المعتاد|العادي|كالعادة|زي",
    re.IGNORECASE,
)


# ======================= قاعدة البيانات =======================

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS group_messages (
        id BIGSERIAL PRIMARY KEY,
        chat_id VARCHAR(100) NOT NULL,
        message_id VARCHAR(120) NOT NULL,
        sender_phone VARCHAR(30),
        sender_name VARCHAR(150),
        is_staff BOOLEAN DEFAULT FALSE,
        body TEXT DEFAULT '',
        media_type VARCHAR(60),
        quoted_message_id VARCHAR(120),
        quoted_text TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (chat_id, message_id)
    );""",
    "CREATE INDEX IF NOT EXISTS idx_group_messages_chat_time ON group_messages (chat_id, created_at DESC);",
    """CREATE TABLE IF NOT EXISTS customer_order_profiles (
        customer_id INTEGER PRIMARY KEY REFERENCES customer_accounts(id) ON DELETE CASCADE,
        product_aliases TEXT DEFAULT '',
        default_branch VARCHAR(150) DEFAULT '',
        default_unit VARCHAR(30) DEFAULT 'Cartons',
        ai_notes TEXT DEFAULT '',
        lead_days INTEGER DEFAULT 1,
        reminder_enabled BOOLEAN DEFAULT FALSE,
        reminder_days VARCHAR(20) DEFAULT '0,1,2,3,4,5,6',
        reminder_time VARCHAR(5) DEFAULT '09:00',
        reminder_text TEXT DEFAULT '',
        last_reminder_date DATE
    );""",
    """CREATE TABLE IF NOT EXISTS order_batches (
        id SERIAL PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
        delivery_date DATE NOT NULL,
        status VARCHAR(20) DEFAULT 'active',
        dispatched_snapshot JSONB DEFAULT '[]'::jsonb,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (customer_id, delivery_date)
    );""",
    """CREATE TABLE IF NOT EXISTS order_lines (
        id SERIAL PRIMARY KEY,
        batch_id INTEGER NOT NULL REFERENCES order_batches(id) ON DELETE CASCADE,
        branch_name VARCHAR(150) NOT NULL,
        product VARCHAR(200) NOT NULL,
        quantity NUMERIC(10, 2) NOT NULL,
        unit VARCHAR(30) DEFAULT 'Cartons',
        source_message_id VARCHAR(120),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (batch_id, branch_name, product)
    );""",
    """CREATE TABLE IF NOT EXISTS pending_order_confirmations (
        id SERIAL PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
        chat_id VARCHAR(100) NOT NULL,
        source_message_id VARCHAR(120),
        proposal JSONB NOT NULL,
        question_text TEXT DEFAULT '',
        status VARCHAR(20) DEFAULT 'pending',
        resolved_by VARCHAR(150),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP WITH TIME ZONE
    );""",
    "CREATE INDEX IF NOT EXISTS idx_pending_chat ON pending_order_confirmations (chat_id, status, created_at DESC);",
    "ALTER TABLE customer_order_profiles ADD COLUMN IF NOT EXISTS products JSONB DEFAULT '[]'::jsonb;",
    # لغة رسائل البوت: للعميل (auto = نفس لغة رسالته) ولكل عضو في فريق المبيعات
    "ALTER TABLE customer_accounts ADD COLUMN IF NOT EXISTS language VARCHAR(4) DEFAULT 'auto';",
    "ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS language VARCHAR(2) DEFAULT 'en';",
    # مسار الاعتماد: العميل ← إدخال البيانات (رقم الطلب في النظام) ← اعتماد المالية ← اللوجستيك
    "ALTER TABLE order_batches ADD COLUMN IF NOT EXISTS erp_order_no VARCHAR(60);",
    "ALTER TABLE order_batches ADD COLUMN IF NOT EXISTS entry_snapshot JSONB;",
    # الطلبيات التي وصلت اللوجستيك قبل تفعيل المسار تُعتبر مُدخلة (لا تُرسل لإدخال البيانات كطلب جديد)
    "UPDATE order_batches SET entry_snapshot = COALESCE(dispatched_snapshot, '[]'::jsonb) WHERE entry_snapshot IS NULL;",
    "ALTER TABLE order_batches ALTER COLUMN entry_snapshot SET DEFAULT '[]'::jsonb;",
    """CREATE TABLE IF NOT EXISTS order_submissions (
        id SERIAL PRIMARY KEY,
        batch_id INTEGER NOT NULL REFERENCES order_batches(id) ON DELETE CASCADE,
        customer_id INTEGER NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
        kind VARCHAR(10) NOT NULL,
        lines JSONB NOT NULL DEFAULT '[]'::jsonb,
        prev_lines JSONB NOT NULL DEFAULT '[]'::jsonb,
        status VARCHAR(12) NOT NULL DEFAULT 'awaiting',
        erp_order_no VARCHAR(60),
        entry_message TEXT DEFAULT '',
        resolved_by VARCHAR(150),
        note TEXT DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        entered_at TIMESTAMP WITH TIME ZONE,
        resolved_at TIMESTAMP WITH TIME ZONE,
        logistics_sent_at TIMESTAMP WITH TIME ZONE
    );""",
    "CREATE INDEX IF NOT EXISTS idx_order_submissions_open ON order_submissions (status, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_order_submissions_batch ON order_submissions (batch_id, id);",
    "ALTER TABLE customer_order_profiles ADD COLUMN IF NOT EXISTS auto_reply_enabled BOOLEAN DEFAULT TRUE;",
    "ALTER TABLE customer_order_profiles ADD COLUMN IF NOT EXISTS reminder_frequency VARCHAR(10) DEFAULT 'daily';",
    # الشكاوى والاستفسارات: تُصنَّف حسب المنتج والنوع لتقارير الجودة والبحث والتطوير
    """CREATE TABLE IF NOT EXISTS customer_issues (
        id SERIAL PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
        chat_id VARCHAR(100) NOT NULL,
        message_id VARCHAR(120),
        issue_type VARCHAR(20) NOT NULL,
        category VARCHAR(40) DEFAULT 'other',
        product VARCHAR(200) DEFAULT '',
        severity VARCHAR(10) DEFAULT 'medium',
        summary TEXT DEFAULT '',
        customer_text TEXT DEFAULT '',
        bot_reply TEXT DEFAULT '',
        status VARCHAR(20) DEFAULT 'open',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP WITH TIME ZONE
    );""",
    "CREATE INDEX IF NOT EXISTS idx_issues_status ON customer_issues (status, created_at DESC);",
    # أسعار العملاء: السعر يُحفظ في سطر الطلب لحظة تسجيله، فلا تتغير قيمة الطلبيات السابقة إذا تغيّر السعر لاحقاً
    "ALTER TABLE order_lines ADD COLUMN IF NOT EXISTS unit_price NUMERIC(12, 3);",
    "ALTER TABLE order_lines ADD COLUMN IF NOT EXISTS price_unit VARCHAR(30);",
    "ALTER TABLE order_lines ADD COLUMN IF NOT EXISTS line_value NUMERIC(14, 3);",
    "ALTER TABLE customer_order_profiles ADD COLUMN IF NOT EXISTS share_prices_with_bot BOOLEAN DEFAULT FALSE;",
    """CREATE TABLE IF NOT EXISTS customer_price_history (
        id SERIAL PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customer_accounts(id) ON DELETE CASCADE,
        product_sku VARCHAR(100),
        product VARCHAR(200) NOT NULL,
        old_price NUMERIC(12, 3),
        new_price NUMERIC(12, 3),
        price_unit VARCHAR(30),
        changed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );""",
]


def init_schema(run_ddl):
    for stmt in SCHEMA:
        run_ddl(stmt)


def now_local() -> datetime:
    return datetime.now(BUSINESS_TZ)


def today_local() -> date:
    return now_local().date()


# ======================= ملف طلب العميل =======================

PROFILE_DEFAULTS = {
    "product_aliases": "", "default_branch": "", "default_unit": "Cartons", "ai_notes": "",
    "lead_days": 1, "reminder_enabled": False, "reminder_days": "0,1,2,3,4,5,6",
    "reminder_time": "09:00", "reminder_text": "", "last_reminder_date": None,
    "products": [], "auto_reply_enabled": True, "reminder_frequency": "daily",
    "share_prices_with_bot": False,
}


def load_profile(cur, customer_id: int) -> dict:
    cur.execute("SELECT * FROM customer_order_profiles WHERE customer_id = %s;", (customer_id,))
    row = cur.fetchone()
    profile = dict(PROFILE_DEFAULTS)
    if row:
        profile.update({k: v for k, v in dict(row).items() if v is not None or k == "last_reminder_date"})
    profile["customer_id"] = customer_id
    return profile


def save_profile(cur, customer_id: int, data: dict):
    fields = [k for k in PROFILE_DEFAULTS if k != "last_reminder_date" and k in data]
    if not fields:
        return
    cols = ", ".join(["customer_id"] + fields)
    vals = ", ".join(["%s"] * (len(fields) + 1))
    updates = ", ".join(f"{f} = EXCLUDED.{f}" for f in fields)
    cur.execute(
        f"INSERT INTO customer_order_profiles ({cols}) VALUES ({vals}) "
        f"ON CONFLICT (customer_id) DO UPDATE SET {updates};",
        [customer_id] + [Json(data[f]) if f == "products" else data[f] for f in fields],
    )


def product_map(profile: dict) -> list:
    """
    منتجات العميل: من قائمة "منتجات العميل" في ملفه (الاسم الذي يستخدمه العميل ← منتجنا ← الوحدة)،
    إضافة إلى الصيغة النصية القديمة إن وُجدت.
    """
    out = []
    for p in profile.get("products") or []:
        product = (p.get("product") or "").strip()
        if not product:
            continue
        price = p.get("price")
        out.append({"alias": (p.get("alias") or product).strip(), "product": product, "sku": (p.get("sku") or "").strip(),
                    "unit": (p.get("unit") or "").strip(), "notes": (p.get("notes") or "").strip(),
                    "price": float(price) if price not in (None, "") else None,
                    "price_unit": (p.get("price_unit") or p.get("unit") or "").strip()})
    out += parse_aliases(profile.get("product_aliases", ""))
    return out


def parse_aliases(text: str) -> list:
    """
    صيغة سطر الصنف: "اسم العميل = اسم الصنف لدينا | الوحدة"
    مثال: 5" = Burger Bun 5 inch (3pc/pkt) | Packets
    """
    out = []
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        alias, rest = line.split("=", 1)
        product, _, unit = rest.partition("|")
        if alias.strip() and product.strip():
            out.append({"alias": alias.strip(), "product": product.strip(), "unit": unit.strip()})
    return out


# ======================= ذاكرة المحادثة =======================

def record_message(cur, chat_id: str, message_id: str, sender_phone: str, sender_name: str, is_staff: bool,
                   body: str, media_type: Optional[str] = None, quoted_message_id: Optional[str] = None,
                   quoted_text: Optional[str] = None):
    cur.execute("""
        INSERT INTO group_messages (chat_id, message_id, sender_phone, sender_name, is_staff, body,
                                    media_type, quoted_message_id, quoted_text)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chat_id, message_id) DO UPDATE SET body = EXCLUDED.body, media_type = EXCLUDED.media_type;
    """, (chat_id, message_id, sender_phone, (sender_name or "")[:150], is_staff, body or "",
          media_type, quoted_message_id, (quoted_text or "")[:1000] or None))


def record_bot_message(cur, chat_id: str, text: str):
    record_message(cur, chat_id, f"bot-{uuid.uuid4().hex[:16]}", "", BOT_NAME, True, text)


def get_context(cur, chat_id: str, exclude_message_id: str) -> list:
    cur.execute("""
        SELECT message_id, sender_name, is_staff, body, media_type, quoted_text, created_at
        FROM group_messages
        WHERE chat_id = %s AND message_id <> %s AND created_at > NOW() - (%s || ' hours')::interval
        ORDER BY created_at DESC LIMIT %s;
    """, (chat_id, exclude_message_id or "", str(CONTEXT_HOURS), CONTEXT_MESSAGES))
    return list(reversed(cur.fetchall()))


def format_context(rows: list) -> str:
    lines = []
    for r in rows:
        ts = r["created_at"].astimezone(BUSINESS_TZ).strftime("%a %d/%m %H:%M")
        who = "STAFF" if r["is_staff"] else "CUSTOMER"
        body = (r["body"] or "").strip().replace("\n", " ⏎ ")
        if r["media_type"]:
            body = f"[{r['media_type']}] {body}".strip()
        quoted = f' (replying to: "{(r["quoted_text"] or "")[:120]}")' if r["quoted_text"] else ""
        lines.append(f"[{ts}] {who} {r['sender_name']}: {body[:400]}{quoted}")
    return "\n".join(lines) or "(no recent messages)"


def staff_wrote_today(cur, chat_id: str) -> bool:
    start = datetime.combine(today_local(), datetime.min.time(), tzinfo=BUSINESS_TZ)
    cur.execute("""
        SELECT 1 FROM group_messages
        WHERE chat_id = %s AND is_staff = TRUE AND sender_name <> %s AND created_at >= %s LIMIT 1;
    """, (chat_id, BOT_NAME, start))
    return cur.fetchone() is not None


# ======================= الطلبيات (طلبية اليوم) =======================

def get_upcoming_batches(cur, customer_id: int) -> list:
    cur.execute("""
        SELECT b.id, b.delivery_date, b.status,
               COALESCE(json_agg(json_build_object('branch', l.branch_name, 'product', l.product,
                        'quantity', l.quantity, 'unit', l.unit) ORDER BY l.branch_name, l.product)
                        FILTER (WHERE l.id IS NOT NULL), '[]') AS lines
        FROM order_batches b LEFT JOIN order_lines l ON l.batch_id = b.id
        WHERE b.customer_id = %s AND b.delivery_date >= %s AND b.status = 'active'
        GROUP BY b.id ORDER BY b.delivery_date LIMIT 5;
    """, (customer_id, today_local() - timedelta(days=1)))
    return cur.fetchall()


def get_habitual_products(cur, customer_id: int) -> list:
    cur.execute("""
        SELECT product, unit, branch_name, COUNT(*) AS n FROM order_lines l
        JOIN order_batches b ON b.id = l.batch_id
        WHERE b.customer_id = %s GROUP BY product, unit, branch_name ORDER BY n DESC LIMIT 12;
    """, (customer_id,))
    rows = [f"{r['product']} ({r['unit']}) @ {r['branch_name']}" for r in cur.fetchall()]
    if not rows:
        cur.execute("""
            SELECT item_name, unit, branch_name, COUNT(*) AS n FROM dispatched_orders
            WHERE customer_id = %s GROUP BY item_name, unit, branch_name ORDER BY n DESC LIMIT 8;
        """, (customer_id,))
        rows = [f"{r['item_name']} ({r['unit']}) @ {r['branch_name']}" for r in cur.fetchall()]
    return rows


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(branch|br)\b|فرع", " ", s)
    return re.sub(r"[^\w؀-ۿ]+", "", s)


def match_branch(name: str, registered: list, aliases: Optional[dict] = None) -> Optional[str]:
    """
    مطابقة اسم الفرع مع الفروع المسجلة (بدون إنشاء فرع جديد).
    الترتيب: الاسم الرسمي مطابقة تامة ← الأسماء البديلة مطابقة تامة ← احتواء جزئي للاسم الرسمي.
    """
    key = _norm(name)
    if not key:
        return None
    for b in registered:
        if _norm(b) == key:
            return b
    by_alias = branch_import.match_alias(_norm, name, aliases)
    if by_alias:
        return by_alias
    for b in registered:
        nb = _norm(b)
        if nb and (key in nb or nb in key):
            return b
    return None


def canonical_branch(cur, customer_id: int, name: str, registered: list, default_branch: str,
                     aliases: Optional[dict] = None) -> str:
    name = (name or "").strip()
    if not name:
        name = default_branch or (registered[0] if len(registered) == 1 else "Main Branch")
    found = match_branch(name, registered, aliases)
    if found:
        return found
    # فرع جديد يُسجَّل تلقائياً للعميل
    try:
        cur.execute("INSERT INTO customer_branches (customer_id, branch_name, location_url) VALUES (%s, %s, '');",
                    (customer_id, name[:150]))
        registered.append(name)
    except Exception as e:
        logger.warning(f"Auto-create branch failed: {e}")
    return name


def canonical_product(name: str, aliases: list) -> tuple:
    """(اسم منتجنا، الوحدة المعتادة، كود الكتالوج) من قائمة منتجات العميل"""
    key = _norm(name)
    for a in aliases:
        if _norm(a["alias"]) == key or _norm(a["product"]) == key or (a.get("sku") and _norm(a["sku"]) == key):
            return a["product"], a["unit"], a.get("sku") or ""
    return (name or "").strip(), "", ""


def get_or_create_batch(cur, customer_id: int, delivery: date) -> dict:
    cur.execute("""
        INSERT INTO order_batches (customer_id, delivery_date) VALUES (%s, %s)
        ON CONFLICT (customer_id, delivery_date) DO UPDATE SET updated_at = NOW()
        RETURNING id, delivery_date, status, dispatched_snapshot;
    """, (customer_id, delivery))
    batch = dict(cur.fetchone())
    if batch["status"] != "active":
        cur.execute("UPDATE order_batches SET status = 'active', dispatched_snapshot = '[]'::jsonb WHERE id = %s;", (batch["id"],))
        batch["status"], batch["dispatched_snapshot"] = "active", []
    return batch


def batch_lines(cur, batch_id: int) -> list:
    cur.execute("""
        SELECT branch_name, product, quantity, unit, product_sku, line_value FROM order_lines
        WHERE batch_id = %s ORDER BY branch_name, product;
    """, (batch_id,))
    return [{"branch": r["branch_name"], "product": r["product"], "quantity": float(r["quantity"]), "unit": r["unit"],
             "sku": r["product_sku"], "value": float(r["line_value"]) if r["line_value"] is not None else None}
            for r in cur.fetchall()]


def customer_price(aliases: list, sku: Optional[str], product: str) -> Optional[dict]:
    """سعر العميل للمنتج من ملفه (بالكود أولاً ثم بالاسم)"""
    for a in aliases:
        if a.get("price") is None:
            continue
        if (sku and a.get("sku") == sku) or _norm(a["product"]) == _norm(product):
            return a
    return None


def price_batch_lines(cur, batch_id: int, aliases: list):
    """
    تسعير أسطر الطلبية:
    - السطر المسعَّر سابقاً يحتفظ بسعره (لقطة وقت الطلب) وتُعاد حساب قيمته فقط إذا تغيرت الكمية.
    - السطر الجديد يأخذ سعر العميل الحالي من ملفه.
    """
    cur.execute("""SELECT id, product, product_sku, quantity, unit, unit_price, price_unit
                   FROM order_lines WHERE batch_id = %s;""", (batch_id,))
    cache = {}
    for ln in cur.fetchall():
        unit_price, price_unit = ln["unit_price"], ln["price_unit"]
        if unit_price is None:
            entry = customer_price(aliases, ln["product_sku"], ln["product"])
            if not entry:
                continue
            unit_price, price_unit = entry["price"], entry["price_unit"] or ln["unit"]
        sku = ln["product_sku"]
        if sku not in cache:
            cache[sku] = catalog.get_by_sku(cur, sku)
        value = catalog.line_value(cache[sku], float(ln["quantity"]), ln["unit"], float(unit_price), price_unit)
        cur.execute("UPDATE order_lines SET unit_price = %s, price_unit = %s, line_value = %s WHERE id = %s;",
                    (unit_price, price_unit, value, ln["id"]))


def lines_totals(cur, lines: list) -> dict:
    """
    مجموع الطلبية بوحدة موحدة: عدد الحبات والوزن بالكيلوجرام (من مواصفات الكتالوج)،
    حتى تُجمع طلبيات الكرتون والكيس في رقم واحد. الأصناف بلا مواصفات تُذكر في unconverted.
    """
    pieces = weight = 0.0
    unconverted, cache = [], {}
    for ln in lines:
        sku = ln.get("sku")
        if sku not in cache:
            cache[sku] = catalog.get_by_sku(cur, sku)
        m = catalog.line_metrics(cache[sku], float(ln["quantity"]), ln["unit"])
        if m["pieces"] is None:
            unconverted.append(ln["product"])
            continue
        pieces += m["pieces"]
        weight += m["weight_kg"] or 0
    values = [ln.get("value") for ln in lines]
    priced = [v for v in values if v is not None]
    return {"pieces": round(pieces) if lines and not unconverted else (round(pieces) if pieces else None),
            "weight_kg": round(weight, 1) if weight else None,
            "complete": bool(lines) and not unconverted, "unconverted": sorted(set(unconverted)),
            "value_omr": round(sum(priced), 3) if priced else None,
            "value_complete": bool(lines) and len(priced) == len(lines),
            "unpriced": sorted({ln["product"] for ln in lines if ln.get("value") is None})}


def resolve_delivery_date(value: Optional[str], profile: dict) -> date:
    today = today_local()
    default = today + timedelta(days=int(profile.get("lead_days") or 1))
    try:
        d = date.fromisoformat((value or "").strip()[:10])
        if today - timedelta(days=1) <= d <= today + timedelta(days=30):
            return d
    except Exception:
        pass
    return default


def apply_proposal(cur, customer: dict, profile: dict, proposal: dict, source_message_id: str) -> Optional[dict]:
    """
    تطبيق العملية على طلبية تاريخ التسليم:
    - new_order: تستبدل أصناف الفروع المذكورة (إعادة إرسال الجدول لا تضاعف الكميات)
    - addition: تضيف على الكمية الحالية
    - modification: تضبط الكمية المطلقة (0 = حذف الصنف)
    - cancellation: تحذف الأسطر المذكورة، أو الطلبية كاملة إذا لم تُذكر أسطر
    يعيد {batch_id, delivery_date, before, after} أو None إذا لم يتغير شيء.
    """
    kind = proposal.get("kind")
    delivery = resolve_delivery_date(proposal.get("delivery_date"), profile)
    aliases = product_map(profile)
    cur.execute("SELECT branch_name, aliases FROM customer_branches WHERE customer_id = %s;", (customer["id"],))
    branch_rows = cur.fetchall()
    registered = [r["branch_name"] for r in branch_rows]
    branch_aliases = {r["branch_name"]: r.get("aliases") or "" for r in branch_rows}

    batch = get_or_create_batch(cur, customer["id"], delivery)
    before = batch_lines(cur, batch["id"])

    norm_lines = []
    for ln in proposal.get("lines") or []:
        try:
            qty = float(ln.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        product, alias_unit, sku = canonical_product(ln.get("product", ""), aliases)
        if not product:
            continue
        sku = sku or catalog.find_sku(cur, product)
        branch = canonical_branch(cur, customer["id"], ln.get("branch", ""), registered, profile.get("default_branch", ""),
                                  branch_aliases)
        unit = (ln.get("unit") or alias_unit or profile.get("default_unit") or "Cartons").strip()[:30]
        norm_lines.append({"branch": branch, "product": product[:200], "quantity": qty, "unit": unit, "sku": sku})

    if kind == "cancellation" and not norm_lines:
        cur.execute("DELETE FROM order_lines WHERE batch_id = %s;", (batch["id"],))
    elif kind == "new_order":
        for branch in {l["branch"] for l in norm_lines}:
            cur.execute("DELETE FROM order_lines WHERE batch_id = %s AND branch_name = %s;", (batch["id"], branch))
    for ln in norm_lines:
        if kind == "cancellation" or (kind == "modification" and ln["quantity"] <= 0):
            cur.execute("DELETE FROM order_lines WHERE batch_id = %s AND branch_name = %s AND product = %s;",
                        (batch["id"], ln["branch"], ln["product"]))
            continue
        if ln["quantity"] <= 0:
            continue
        on_conflict = "order_lines.quantity + EXCLUDED.quantity" if kind == "addition" else "EXCLUDED.quantity"
        cur.execute(f"""
            INSERT INTO order_lines (batch_id, branch_name, product, quantity, unit, source_message_id, product_sku)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (batch_id, branch_name, product) DO UPDATE SET
                quantity = {on_conflict}, unit = EXCLUDED.unit, product_sku = EXCLUDED.product_sku,
                source_message_id = EXCLUDED.source_message_id, updated_at = NOW();
        """, (batch["id"], ln["branch"], ln["product"], ln["quantity"], ln["unit"], source_message_id, ln.get("sku") or None))

    price_batch_lines(cur, batch["id"], aliases)
    after = batch_lines(cur, batch["id"])
    if after == before:
        return None
    if not after:
        cur.execute("UPDATE order_batches SET status = 'cancelled', updated_at = NOW() WHERE id = %s;", (batch["id"],))
    sync_dispatched_orders(cur, customer, batch["id"], delivery, after)
    return {"batch_id": batch["id"], "delivery_date": delivery, "before": before, "after": after,
            "snapshot": batch.get("dispatched_snapshot") or []}


def sync_dispatched_orders(cur, customer: dict, batch_id: int, delivery: date, lines: list):
    """مزامنة جدول الإحصائيات القديم (dispatched_orders) حتى تبقى التحليلات الحالية تعمل"""
    # مرجع لكل (طلبية + فرع) حتى يبقى اسم الصنف نظيفاً في التحليلات مع احترام قيد التفرد (message_id, item_name)
    cur.execute("DELETE FROM dispatched_orders WHERE message_id LIKE %s;", (f"batch-{batch_id}:%",))
    for ln in lines:
        branch_ref = hashlib.md5(ln["branch"].encode("utf-8")).hexdigest()[:10]
        cur.execute("""
            INSERT INTO dispatched_orders (customer_id, customer_name, branch_name, order_date, item_name,
                                           quantity, unit, message_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id, item_name) WHERE message_id IS NOT NULL
            DO UPDATE SET quantity = dispatched_orders.quantity + EXCLUDED.quantity;
        """, (customer["id"], customer["company_name"], ln["branch"], delivery,
              ln["product"][:200], int(round(ln["quantity"])), ln["unit"], f"batch-{batch_id}:{branch_ref}"))


def remove_lines_from_message(cur, customer: dict, profile: dict, message_id: str) -> Optional[dict]:
    """حذف/تعديل رسالة من العميل: إزالة الأسطر التي أنشأتها تلك الرسالة"""
    cur.execute("""
        SELECT DISTINCT b.id, b.delivery_date, b.dispatched_snapshot FROM order_lines l
        JOIN order_batches b ON b.id = l.batch_id
        WHERE b.customer_id = %s AND l.source_message_id = %s;
    """, (customer["id"], message_id))
    row = cur.fetchone()
    if not row:
        return None
    before = batch_lines(cur, row["id"])
    cur.execute("DELETE FROM order_lines WHERE batch_id = %s AND source_message_id = %s;", (row["id"], message_id))
    after = batch_lines(cur, row["id"])
    if not after:
        cur.execute("UPDATE order_batches SET status = 'cancelled', updated_at = NOW() WHERE id = %s;", (row["id"],))
    sync_dispatched_orders(cur, customer, row["id"], row["delivery_date"], after)
    return {"batch_id": row["id"], "delivery_date": row["delivery_date"], "before": before, "after": after,
            "snapshot": row["dispatched_snapshot"] or []}


# ======================= رسائل اللوجستيك =======================

def _qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


def _group_by_branch(lines: list) -> dict:
    out = {}
    for ln in lines:
        out.setdefault(ln["branch"], []).append(ln)
    return out


def _totals(lines: list, metrics: Optional[dict] = None, lang: str = "en") -> str:
    totals = {}
    for ln in lines:
        totals[ln["unit"]] = totals.get(ln["unit"], 0) + ln["quantity"]
    text = " + ".join(f"{_qty(v)} {k}" for k, v in totals.items()) or "0"
    if metrics and metrics.get("complete") and metrics.get("pieces"):
        text += (f"  (≈ {metrics['pieces']:,} {i18n.t('pcs', lang)}" +
                 (f" · {metrics['weight_kg']:,} {i18n.t('kg', lang)}" if metrics.get("weight_kg") else "") + ")")
    return text


def format_logistics_message(customer: dict, change: dict, ref: Optional[str] = None,
                             extra_lines: Optional[list] = None, footer: str = "", lang: str = "en",
                             cancel_note: str = "cancel_note_logistics") -> str:
    """
    رسالة الطلب (جديد/تحديث/إلغاء) بالإنجليزية أو العربية. تُستخدم للوجستيك ولموظف إدخال البيانات:
    ref = مرجع الرسالة (افتراضياً B-رقم الطلبية)، extra_lines = أسطر بعد التاريخ (رقم الطلب والحالة)،
    footer = تعليمات في آخر الرسالة، cancel_note = نص الإلغاء المناسب للمستلم.
    """
    T = lambda key, **kw: i18n.t(key, lang, **kw)
    after, snapshot = change["after"], change["snapshot"] or []
    d: date = change["delivery_date"]
    header_name = customer["company_name"] + (f" ({customer['brand_name']})" if customer.get("brand_name") else "")
    date_line = f"{T('delivery')} {i18n.fmt_date(d, lang)}" + "".join("\n" + x for x in (extra_lines or []))
    ref_line = f"{T('ref')} {ref or 'B-' + str(change['batch_id'])}" + (f"\n{footer}" if footer else "")
    sep = "----------------------------------------"

    if not after:
        return f"{T('order_cancelled')} | {header_name}\n{date_line}\n{sep}\n{T(cancel_note)}\n{ref_line}"

    body = []
    for branch, items in _group_by_branch(after).items():
        body.append(f"📍 *{branch}*")
        body += [f"   • {i['product']}: {_qty(i['quantity'])} {i['unit']}" for i in items]
    current = "\n".join(body)
    total = f"{T('total')} {_totals(after, change.get('totals'), lang)}"

    if not snapshot:
        return f"{T('new_order')} | {header_name}\n{date_line}\n{sep}\n{current}\n{sep}\n{total}\n{ref_line}"

    prev = {(l["branch"], l["product"]): l for l in snapshot}
    now = {(l["branch"], l["product"]): l for l in after}
    changes = []
    for key, l in now.items():
        if key not in prev:
            changes.append(f"   ➕ {l['branch']} – {l['product']}: {_qty(l['quantity'])} {l['unit']}")
        elif float(prev[key]["quantity"]) != float(l["quantity"]):
            changes.append(f"   ✏️ {l['branch']} – {l['product']}: {_qty(float(prev[key]['quantity']))} → {_qty(l['quantity'])} {l['unit']}")
    for key, l in prev.items():
        if key not in now:
            changes.append(f"   ❌ {l['branch']} – {l['product']}: {T('removed')}")
    return (f"{T('order_update')} | {header_name}\n{date_line}\n{sep}\n{T('changes')}\n" + "\n".join(changes) +
            f"\n{sep}\n{T('full_now')}\n{current}\n{total}\n{ref_line}")


def mark_dispatched(cur, change: dict):
    cur.execute("UPDATE order_batches SET dispatched_snapshot = %s::jsonb, updated_at = NOW() WHERE id = %s;",
                (json.dumps(change["after"]), change["batch_id"]))


def summarize_lines(lines: list, lang: str = "ar") -> str:
    parts = []
    for branch, items in _group_by_branch(lines).items():
        its = "، ".join(f"{_qty(i['quantity'])} {i['unit']} {i['product']}" for i in items) if lang == "ar" else \
              ", ".join(f"{_qty(i['quantity'])} {i['unit']} {i['product']}" for i in items)
        parts.append(f"{branch}: {its}")
    return " | ".join(parts)


# ======================= فهم الرسالة بالذكاء الاصطناعي =======================

INTERPRET_TOOL = {
    "name": "record_interpretation",
    "description": "Record how the latest customer WhatsApp message affects the bakery orders.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["new_order", "addition", "modification", "cancellation", "confirm_yes", "confirm_no",
                         "complaint", "inquiry", "not_order"],
                "description": (
                    "new_order: a (re)statement of the order for the branches it mentions. "
                    "addition: adds quantities on top of an existing order (e.g. 'Additional', 'could you add'). "
                    "modification: sets new absolute quantities for specific items (e.g. '5 packets only'). "
                    "cancellation: cancel items or the whole order. "
                    "confirm_yes / confirm_no: answer to a pending confirmation question from us. "
                    "complaint: any dissatisfaction about product quality, freshness, size, taste, packaging, delivery time, missing/short quantities, wrong items, staff. "
                    "inquiry: a question we should answer (product info, prices, availability, delivery schedule, ordering process, company info, account/invoice). "
                    "not_order: greetings, thanks, chit-chat, acknowledgements, or messages between the customer's own staff."
                ),
            },
            "delivery_date": {"type": "string", "description": "Delivery date YYYY-MM-DD if stated or clearly implied (e.g. 'Coming 19.11', 'tomorrow', table header). Empty if unknown."},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "branch": {"type": "string", "description": "Branch name; prefer the exact registered branch name."},
                        "product": {"type": "string", "description": "Our product name; use the alias mapping when the customer uses their own names."},
                        "quantity": {"type": "number"},
                        "unit": {"type": "string", "description": "Cartons, Packets, Boxes, Pieces..."},
                    },
                    "required": ["branch", "product", "quantity", "unit"],
                },
            },
            "confidence": {"type": "number", "description": "0..1 confidence that kind AND every line (branch, product, quantity, unit) are correct."},
            "needs_confirmation": {"type": "boolean", "description": "True if anything was guessed (product, branch, unit or date), or if the customer asks us to confirm availability."},
            "ambiguity": {"type": "string", "enum": ["none", "interpretation", "availability"],
                          "description": "interpretation: we guessed and should ask the customer. availability: the customer asked us to confirm we can supply."},
            "clarification_question": {"type": "string", "description": "If ambiguity=interpretation: one short polite question to the customer IN THE CUSTOMER'S LANGUAGE restating your best interpretation, e.g. 'هل تقصدون 45 كرتون بريوش لفرع صلالة لتسليم الخميس 19/11؟'"},
            "customer_language": {"type": "string", "enum": ["ar", "en"]},
            "issue_category": {"type": "string",
                               "enum": ["quality", "freshness", "size_weight", "packaging", "delivery", "quantity", "wrong_item",
                                        "price", "product_info", "availability", "account_invoice", "company_info", "other"],
                               "description": "For complaint/inquiry only."},
            "issue_product": {"type": "string", "description": "For complaint/inquiry: our product concerned, if any."},
            "issue_severity": {"type": "string", "enum": ["low", "medium", "high"], "description": "For complaints: high = food safety, mould, foreign object, or customer threatens to stop ordering."},
            "issue_summary": {"type": "string", "description": "For complaint/inquiry: one-sentence summary IN ARABIC for our team."},
            "reasoning": {"type": "string", "description": "One short sentence."},
        },
        "required": ["kind", "lines", "confidence", "needs_confirmation", "ambiguity", "customer_language"],
    },
}

SYSTEM_PROMPT = """You are the order desk of Food Development Co. (FDC), a B2B industrial bakery in Oman.
You read messages from customer WhatsApp groups and turn them into structured order operations.

How these customers write:
- Formats vary and are often extremely short. A bare number ("45", "30 ctn") usually answers a recent STAFF message asking for the order; use the conversation, the customer's habitual products and default branch to fill in what is missing.
- Some send a PHOTO of a spreadsheet: rows = branches, columns = products (their own product names), cells = quantities. Read every row. Ignore zero/empty cells and rows highlighted in red with no numbers. Headers may contain the delivery date (e.g. 'DELIVERY DATE 19 thursday').
- Follow-up text after a table usually ADDS to it ('Additional', 'could you add') or CHANGES one cell ('... 5 packets only').
- Forwarded messages may contain an order date and a different delivery date ('Coming 19.11'): use the delivery date.
- Arabic and English are mixed. 'كرتون/كراتين/ctn/cartoon/box' are cartons; 'pkt/packets/اكياس' are packets.
Rules:
- Branch names: output EXACTLY one of the REGISTERED BRANCHES when the customer means it, translating Arabic/English and fixing spelling (e.g. 'صلالة' -> 'Salalah Branch', 'Kucu sohargate' -> 'KUCU SOHAR GATE'). Only output a new name for a genuinely new branch, and then set needs_confirmation=true.
- Never invent quantities. If the quantity is missing, it is not an order (or ask).
- If you had to guess the product, branch, unit or date, set needs_confirmation=true, ambiguity=interpretation and ask the customer one short question in their language.
- Staff messages and our earlier bot messages are context only; you are classifying the LATEST CUSTOMER message.
- If a confirmation question from us is pending and the customer agrees, kind=confirm_yes; if they correct it, return the corrected order (new_order/modification) instead.
"""


def build_interpretation_request(customer: dict, profile: dict, branches: list, habitual: list, upcoming: list,
                                 context_rows: list, pending: Optional[dict], msg,
                                 branch_aliases: Optional[dict] = None) -> tuple:
    now = now_local()
    # الأسماء البديلة تُعرض بجانب اسم الفرع حتى يعيد Claude الاسم الرسمي مباشرة
    branch_txt = ", ".join(
        b + (f" (also written: {', '.join(branch_import.split_aliases((branch_aliases or {}).get(b)))})"
             if (branch_aliases or {}).get(b) else "")
        for b in branches)
    forced = (customer.get("language") or "auto")
    forced_lang_note = (f"\nLANGUAGE: always write clarification_question for this customer in "
                        f"{'Arabic' if forced == 'ar' else 'English'}." if forced in i18n.LANGS else "")
    aliases = product_map(profile)
    alias_txt = "\n".join(f'  "{a["alias"]}" => {a["product"]}' + (f' (code {a["sku"]})' if a.get("sku") else "")
                          + (f' [{a["unit"]}]' if a["unit"] else "")
                          + (f' ({a["notes"]})' if a.get("notes") else "") for a in aliases) or "  (none)"
    upcoming_txt = "\n".join(
        f"  {b['delivery_date']}: " + (summarize_lines([{**l, 'quantity': float(l['quantity'])} for l in b["lines"]], "en") or "(empty)")
        for b in upcoming) or "  (none)"
    pending_txt = ""
    if pending:
        p = pending["proposal"]
        pending_txt = (f"\nPENDING CONFIRMATION (we asked the customer, awaiting answer): "
                       f"{p.get('kind')} for {p.get('delivery_date') or 'next delivery'}: {summarize_lines(p.get('lines') or [], 'en')}\n"
                       f"Question we asked: {pending.get('question_text') or '(asked staff)'}\n")
    quoted = f'\nThe latest message is a REPLY to: "{msg.quoted_text}"' if msg.quoted_text else ""

    info = f"""TODAY: {now.strftime('%A %Y-%m-%d %H:%M')} (Muscat time). Default delivery if not stated: {(now.date() + timedelta(days=int(profile.get('lead_days') or 1))).isoformat()}.
CUSTOMER: {customer['company_name']} (brand: {customer.get('brand_name') or '-'})
REGISTERED BRANCHES: {branch_txt or '(none)'}
DEFAULT BRANCH: {profile.get('default_branch') or '(none)'}   DEFAULT UNIT: {profile.get('default_unit') or 'Cartons'}
PRODUCTS THIS CUSTOMER ORDERS (customer's name => our product [unit]):
{alias_txt}
HABITUAL PRODUCTS (most frequent first):
  {chr(10).join('  ' + h for h in habitual).strip() or '(no history)'}
NOTES ABOUT THIS CUSTOMER: {profile.get('ai_notes') or '-'}{forced_lang_note}
UPCOMING ORDERS ALREADY RECORDED:
{upcoming_txt}
{pending_txt}
RECENT CONVERSATION (oldest first):
{format_context(context_rows)}

LATEST CUSTOMER MESSAGE from {msg.sender_name}:{quoted}
\"\"\"{(msg.message_text or '').strip() or '(no text)'}\"\"\""""

    content = []
    use_vision = False
    if getattr(msg, "media_base64", None):
        block = ai_client.media_block(msg.media_base64, msg.media_mimetype or "")
        if block:
            content.append(block)
            use_vision = True
            info += "\n(The attached image/document IS the latest customer message.)"
        else:
            # Word / Excel / CSV: يُستخرج النص محلياً ويُرفق
            doc_text = media_tools.extract_text_locally(base64.b64decode(msg.media_base64), msg.media_mimetype or "",
                                                        msg.media_filename or "")
            if doc_text:
                info += f"\nATTACHED FILE ({msg.media_filename or msg.media_mimetype}) IS the latest customer message:\n{doc_text[:8000]}"
    content.append({"type": "text", "text": info})
    return content, use_vision


def should_interpret(text: str, has_media: bool, has_pending: bool) -> bool:
    """التحيات والشكر والرموز فقط لا تحتاج النموذج؛ أي رسالة أخرى قد تكون طلباً أو شكوى أو استفساراً"""
    if has_media or has_pending:
        return True
    text = (text or "").strip()
    if ORDER_HINT_RE.search(text):
        return True
    if len(text) < 4 or TRIVIAL_RE.match(text):
        return False
    return True


def get_latest_pending(cur, chat_id: str) -> Optional[dict]:
    cur.execute("""
        SELECT * FROM pending_order_confirmations
        WHERE chat_id = %s AND status = 'pending' AND created_at > NOW() - (%s || ' hours')::interval
        ORDER BY created_at DESC LIMIT 1;
    """, (chat_id, str(PENDING_TTL_HOURS)))
    row = cur.fetchone()
    return dict(row) if row else None


# ======================= روابط اعتماد المندوب =======================

def _approval_sig(pending_id: int, exp: int) -> str:
    raw = hmac.new(security._SIGNING_KEY, f"approve:{pending_id}:{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:32]


def make_approval_token(pending_id: int) -> str:
    exp = int(time.time()) + PENDING_TTL_HOURS * 3600 + 3600
    return f"{pending_id}.{exp}.{_approval_sig(pending_id, exp)}"


def verify_approval_token(token: str) -> Optional[int]:
    try:
        pid_s, exp_s, sig = token.split(".")
        pid, exp = int(pid_s), int(exp_s)
    except Exception:
        return None
    if exp < time.time() or not hmac.compare_digest(sig, _approval_sig(pid, exp)):
        return None
    return pid


def public_base_url() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    return f"https://{domain}" if domain else ""


# ======================= معالجة الرسائل =======================

def _action(to: str, text: str) -> dict:
    return {"to": to, "text": text}


def _dispatch_change(cur, customer: dict, change: dict, logistics_target: str) -> list:
    if not change:
        return []
    actions = []
    change["totals"] = lines_totals(cur, change["after"])
    entry_target = data_entry_target(cur)
    if entry_target:
        # المسار الجديد: الطلب يذهب أولاً لموظف إدخال البيانات، ولا يصل اللوجستيك إلا بعد اعتماد المالية
        import order_approval
        return order_approval.submit_change(cur, customer, change, entry_target, logistics_target)
    if logistics_target:
        actions.append(_action(logistics_target, format_logistics_message(customer, change, lang=logistics_language(cur))))
    mark_dispatched(cur, change)
    return actions


def get_setting(cur, key: str, default: str = "") -> str:
    cur.execute("SELECT key_value FROM system_config WHERE key_name = %s;", (key,))
    row = cur.fetchone()
    return (row["key_value"] if row and row["key_value"] is not None else default).strip()


def data_entry_language(cur) -> str:
    return i18n.norm(get_setting(cur, "data_entry_language"))


def logistics_language(cur) -> str:
    return i18n.norm(get_setting(cur, "logistics_language"))


def admin_language(cur) -> str:
    return i18n.norm(get_setting(cur, "admin_language"))


def alert_target_lang(cur, customer: dict) -> tuple:
    """(الرقم، اللغة) لمستلم التنبيهات: المندوب بلغته، وإلا مسؤول النظام بلغته"""
    if customer.get("assigned_rep_id"):
        cur.execute("SELECT phone_number, language FROM sales_executives WHERE id = %s;", (customer["assigned_rep_id"],))
        row = cur.fetchone()
        if row and row["phone_number"] and re.sub(r"[^0-9]", "", row["phone_number"]):
            return re.sub(r"[^0-9]", "", row["phone_number"]), i18n.norm(row.get("language"))
    return re.sub(r"[^0-9]", "", get_setting(cur, "admin_alert_phone")), admin_language(cur)


def data_entry_target(cur) -> str:
    """مجموعة أو رقم موظف إدخال البيانات؛ فارغ = الطلبات تذهب مباشرة للوجستيك (السلوك السابق)"""
    return get_setting(cur, "data_entry_target")


def alert_target(cur, customer: dict) -> str:
    return alert_target_lang(cur, customer)[0]


def staff_active_recently(cur, chat_id: str, minutes: int = STAFF_ACTIVE_MINUTES) -> bool:
    """أحد أعضاء الفريق يتحدث في المجموعة الآن: البوت لا يقاطعه بردود آلية"""
    cur.execute("""
        SELECT 1 FROM group_messages
        WHERE chat_id = %s AND is_staff = TRUE AND sender_name <> %s
          AND created_at > NOW() - (%s || ' minutes')::interval LIMIT 1;
    """, (chat_id, BOT_NAME, str(minutes)))
    return cur.fetchone() is not None


def create_pending(cur, customer: dict, chat_id: str, source_message_id: str, proposal: dict) -> tuple:
    """طلب غامض: سؤال العميل في المجموعة (عند الغموض في الفهم) + تنبيه خاص للمندوب برابط اعتماد"""
    question = ""
    if proposal.get("ambiguity") == "interpretation":
        question = (proposal.get("clarification_question") or "").strip()
        if not question:
            clang = i18n.customer_lang(customer, proposal.get("customer_language"))
            question = i18n.t("cu_confirm_q", clang, summary=summarize_lines(proposal.get("lines") or [], clang))
    cur.execute("""
        UPDATE pending_order_confirmations SET status = 'superseded', resolved_at = NOW()
        WHERE chat_id = %s AND status = 'pending';
    """, (chat_id,))
    cur.execute("""
        INSERT INTO pending_order_confirmations (customer_id, chat_id, source_message_id, proposal, question_text)
        VALUES (%s, %s, %s, %s::jsonb, %s) RETURNING id;
    """, (customer["id"], chat_id, source_message_id, json.dumps(proposal, default=str), question))
    pid = cur.fetchone()["id"]

    actions = []
    if question:
        actions.append(_action(chat_id, question))
        record_bot_message(cur, chat_id, question)

    rep, rlang = alert_target_lang(cur, customer)
    if rep:
        link = f"{public_base_url()}/approve/{make_approval_token(pid)}" if public_base_url() else ""
        rep_msg = i18n.t("al_pending", rlang, company=customer["company_name"],
                         reason=i18n.t("al_reason_availability" if proposal.get("ambiguity") == "availability" else "al_reason_unsure", rlang),
                         delivery=proposal.get("delivery_date") or i18n.t("al_default_delivery", rlang),
                         summary=summarize_lines(proposal.get("lines") or [], rlang) or i18n.t("al_no_items", rlang),
                         action="\n" + (i18n.t("al_link", rlang, link=link) if link else i18n.t("al_dashboard", rlang)))
        actions.append(_action(rep, rep_msg))
    return pid, actions


def resolve_pending(cur, pending: dict, approve: bool, resolved_by: str, customer: dict, profile: dict,
                    logistics_target: str, announce_in_group: bool) -> list:
    if pending["status"] != "pending":
        return []
    cur.execute("""
        UPDATE pending_order_confirmations SET status = %s, resolved_by = %s, resolved_at = NOW()
        WHERE id = %s AND status = 'pending' RETURNING id;
    """, ("approved" if approve else "rejected", resolved_by[:150], pending["id"]))
    if not cur.fetchone():
        return []  # اعتُمد للتو من طرف آخر
    if not approve:
        return []
    proposal = pending["proposal"]
    change = apply_proposal(cur, customer, profile, proposal, pending.get("source_message_id") or f"pending-{pending['id']}")
    actions = _dispatch_change(cur, customer, change, logistics_target)
    if announce_in_group:
        note = i18n.t("cu_order_confirmed", i18n.customer_lang(customer, proposal.get("customer_language")))
        actions.append(_action(pending["chat_id"], note))
        record_bot_message(cur, pending["chat_id"], note)
    return actions


# ======================= الشكاوى والاستفسارات =======================

CATEGORY_AR = {
    "quality": "الجودة", "freshness": "الطزاجة", "size_weight": "الحجم/الوزن", "packaging": "التغليف",
    "delivery": "التوصيل", "quantity": "نقص الكمية", "wrong_item": "صنف خاطئ", "price": "الأسعار",
    "product_info": "معلومات منتج", "availability": "التوفر", "account_invoice": "الحساب/الفواتير",
    "company_info": "عن الشركة", "other": "أخرى",
}
SEVERITY_AR = {"low": "منخفضة", "medium": "متوسطة", "high": "🔴 عالية"}


def record_issue(cur, customer: dict, chat_id: str, message_id: str, kind: str, result: dict, text: str) -> int:
    cur.execute("""
        INSERT INTO customer_issues (customer_id, chat_id, message_id, issue_type, category, product, severity,
                                     summary, customer_text)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    """, (customer["id"], chat_id, message_id, kind, (result.get("issue_category") or "other")[:40],
          (result.get("issue_product") or "")[:200], result.get("issue_severity") or ("medium" if kind == "complaint" else "low"),
          result.get("issue_summary") or "", (text or "")[:2000]))
    return cur.fetchone()["id"]


async def handle_issue(conn, customer: dict, profile: dict, msg, kind: str, result: dict, context_rows: list) -> list:
    """
    شكوى: تُسجَّل وتُنبَّه الفريق فوراً؛ مع تفعيل الرد الآلي يُرسل اعتذار وتأكيد استلام فقط (بدون وعود).
    استفسار: مع تفعيل الرد الآلي يجيب البوت من قاعدة المعرفة فقط؛ ما لا يعرفه يُحوَّل للفريق مع رد مؤقت للعميل.
    لا يرد البوت آلياً إذا كان أحد من الفريق يتحدث في المجموعة خلال آخر 10 دقائق.
    """
    chat_id = msg.chat_id.strip()
    text = (msg.message_text or "").strip()
    lang = i18n.customer_lang(customer, result.get("customer_language"))
    actions = []
    with conn.cursor() as cur:
        issue_id = record_issue(cur, customer, chat_id, msg.message_id, kind, result, text)
        auto = (get_setting(cur, "auto_reply_enabled", "false").lower() == "true"
                and profile.get("auto_reply_enabled", True) and not staff_active_recently(cur, chat_id))
        target, alang = alert_target_lang(cur, customer)
        kb_text = knowledge.build_context(cur) if (auto and kind == "inquiry") else ""
        # أسعار هذا العميل فقط (إن سمح المسؤول بذلك في ملفه) - لا تُكشف أسعار العملاء الآخرين أبداً
        if kb_text is not None and auto and kind == "inquiry" and profile.get("share_prices_with_bot"):
            prices = [a for a in product_map(profile) if a.get("price") is not None]
            if prices:
                kb_text += "\n\n=== AGREED PRICES FOR THIS CUSTOMER ONLY (OMR) ===\n" + "\n".join(
                    f"{a['product']}" + (f" (code {a['sku']})" if a.get("sku") else "") +
                    f": {a['price']:.3f} OMR per {a['price_unit'] or a['unit'] or 'unit'}" for a in prices)
    conn.commit()

    category = i18n.category(result.get("issue_category"), alang)
    product = result.get("issue_product") or "-"
    summary = result.get("issue_summary") or text[:200]
    bot_reply, status, alert = "", "open", None

    if kind == "complaint":
        alert = i18n.t("al_complaint", alang, company=customer["company_name"], category=category, product=product,
                       severity=i18n.severity(result.get("issue_severity"), alang), summary=summary,
                       sender=msg.sender_name, text=text[:400])
        if auto:
            bot_reply = i18n.t("cu_complaint", lang)
    else:  # inquiry
        answer = None
        if auto:
            answer = await knowledge.answer_question(kb_text, customer["company_name"], text,
                                                     format_context(context_rows), lang)
        if answer and answer.get("can_answer") and (answer.get("answer") or "").strip():
            bot_reply, status = answer["answer"].strip(), "answered_by_bot"
        else:
            reason = (answer or {}).get("escalation_reason") or (i18n.t("al_autoreply_off", alang) if not auto else "")
            alert = i18n.t("al_inquiry", alang, company=customer["company_name"], category=category, product=product,
                           sender=msg.sender_name, text=text[:400],
                           reason=i18n.t("al_escalation", alang, reason=reason) if reason else "")
            status = "escalated"
            if auto:
                bot_reply = i18n.t("cu_inquiry_hold", lang)

    with conn.cursor() as cur:
        if bot_reply:
            actions.append(_action(chat_id, bot_reply))
            record_bot_message(cur, chat_id, bot_reply)
        if alert and target:
            actions.append(_action(target, alert))
        cur.execute("UPDATE customer_issues SET status = %s, bot_reply = %s WHERE id = %s;", (status, bot_reply, issue_id))
    conn.commit()
    return actions


async def handle_customer_message(conn, customer: dict, msg, logistics_target: str) -> dict:
    """رسالة من العميل في مجموعته: تُفهم في سياق المحادثة وتُطبق على طلبية تاريخ التسليم"""
    chat_id = msg.chat_id.strip()
    message_id = msg.message_id or f"nomsg-{uuid.uuid4().hex[:12]}"

    # الرسائل الصوتية: تُحوَّل إلى نص ثم تُعامل كرسالة نصية عادية
    if getattr(msg, "media_base64", None) and media_tools.kind_of(msg.media_mimetype or "") == "audio":
        transcript = await media_tools.transcribe_audio(msg.media_base64, msg.media_mimetype)
        msg.message_text = (f"[رسالة صوتية] {transcript}" if transcript else "[رسالة صوتية - تعذر التفريغ]") + \
                           (f" {msg.message_text}" if msg.message_text else "")
        msg.media_base64 = None
        msg.media_mimetype = "audio"

    has_media = bool(getattr(msg, "media_base64", None))
    media_type = (msg.media_mimetype or "media").split("/")[0] if (has_media or msg.media_mimetype == "audio") else None

    with conn.cursor() as cur:
        profile = load_profile(cur, customer["id"])
        record_message(cur, chat_id, message_id, msg.sender_phone, msg.sender_name, False,
                       msg.message_text, media_type, msg.quoted_message_id, msg.quoted_text)

        if msg.event_type == "MESSAGE_DELETED":
            change = remove_lines_from_message(cur, customer, profile, message_id)
            actions = _dispatch_change(cur, customer, change, logistics_target)
            conn.commit()
            return {"status": "SMART_DELETE", "actions": actions}

        edit_change = None
        if msg.event_type == "MESSAGE_EDITED":
            edit_change = remove_lines_from_message(cur, customer, profile, message_id)

        pending = get_latest_pending(cur, chat_id)
        text = (msg.message_text or "").strip()

        # ردود التأكيد البسيطة لا تحتاج استدعاء النموذج
        if pending and not has_media and AFFIRMATIVE_RE.match(text):
            actions = resolve_pending(cur, pending, True, f"customer:{msg.sender_name}", customer, profile,
                                      logistics_target, announce_in_group=False)
            conn.commit()
            return {"status": "SMART_CONFIRMED_BY_CUSTOMER", "actions": actions}

        if not should_interpret(text, has_media, bool(pending)):
            conn.commit()
            return {"status": "SMART_STORED", "actions": []}

        cur.execute("SELECT branch_name, aliases FROM customer_branches WHERE customer_id = %s ORDER BY id;", (customer["id"],))
        branch_rows = cur.fetchall()
        branches = [r["branch_name"] for r in branch_rows]
        branch_aliases = {r["branch_name"]: r.get("aliases") or "" for r in branch_rows}
        habitual = get_habitual_products(cur, customer["id"])
        upcoming = get_upcoming_batches(cur, customer["id"])
        context_rows = get_context(cur, chat_id, message_id)
    conn.commit()  # لا نُبقي معاملة مفتوحة أثناء انتظار النموذج

    content, use_vision = build_interpretation_request(customer, profile, branches, habitual, upcoming,
                                                       context_rows, pending, msg, branch_aliases)
    result = await ai_client.call_tool(SYSTEM_PROMPT, content, INTERPRET_TOOL, use_vision=use_vision)
    if result is not None and customer.get("language") in i18n.LANGS:
        result["customer_language"] = customer["language"]  # لغة العميل المحددة في ملفه تتقدّم على المكتشفة
    if not result:
        logger.warning(f"Order interpretation failed for {customer['company_name']} msg {message_id}")
        return {"status": "SMART_AI_UNAVAILABLE", "actions": [], "fallback": True}

    kind = result.get("kind", "not_order")
    logger.info(f"[Orders] {customer['company_name']}: kind={kind} conf={result.get('confidence')} "
                f"ambiguity={result.get('ambiguity')} lines={len(result.get('lines') or [])} :: {result.get('reasoning', '')[:120]}")

    with conn.cursor() as cur:
        actions = []
        if edit_change:
            actions += _dispatch_change(cur, customer, edit_change, logistics_target) if kind not in ORDER_KINDS else []

        if kind == "confirm_yes" and pending:
            actions += resolve_pending(cur, pending, True, f"customer:{msg.sender_name}", customer, profile,
                                       logistics_target, announce_in_group=False)
        elif kind == "confirm_no" and pending:
            actions += resolve_pending(cur, pending, False, f"customer:{msg.sender_name}", customer, profile,
                                       logistics_target, announce_in_group=False)
        elif kind in ("complaint", "inquiry"):
            conn.commit()
            actions += await handle_issue(conn, customer, profile, msg, kind, result, context_rows)
        elif kind in ORDER_KINDS and (result.get("lines") or kind == "cancellation"):
            proposal = {
                "kind": kind,
                "delivery_date": resolve_delivery_date(result.get("delivery_date"), profile).isoformat(),
                "lines": result.get("lines") or [],
                "ambiguity": result.get("ambiguity", "none"),
                "clarification_question": result.get("clarification_question", ""),
                "customer_language": result.get("customer_language", "ar"),
            }
            confident = (float(result.get("confidence") or 0) >= AUTO_CONFIRM_THRESHOLD
                         and not result.get("needs_confirmation") and result.get("ambiguity", "none") == "none")
            # فرع غير مسجل (قد يكون ترجمة لفرع موجود أو فرعاً جديداً فعلاً): لا يُرسل تلقائياً، بل يُؤكَّد أولاً
            if confident and branches and kind != "cancellation":
                fallback = profile.get("default_branch") or (branches[0] if len(branches) == 1 else "")
                unknown = [l for l in proposal["lines"]
                           if not match_branch(l.get("branch") or fallback, branches, branch_aliases)]
                if unknown:
                    confident = False
                    logger.info(f"[Orders] Unregistered branch(es) {[l.get('branch') for l in unknown]} -> confirmation")
            if confident:
                if pending:  # تصحيح من العميل يُغني عن السؤال السابق
                    cur.execute("UPDATE pending_order_confirmations SET status = 'superseded', resolved_at = NOW() WHERE id = %s;",
                                (pending["id"],))
                change = apply_proposal(cur, customer, profile, proposal, message_id)
                if edit_change and change:
                    change["snapshot"] = edit_change["snapshot"]
                actions += _dispatch_change(cur, customer, change, logistics_target)
            else:
                if proposal["ambiguity"] == "none":
                    proposal["ambiguity"] = "interpretation"
                _, pend_actions = create_pending(cur, customer, chat_id, message_id, proposal)
                actions += pend_actions
        conn.commit()
    return {"status": f"SMART_{kind.upper()}", "actions": actions}


def handle_staff_message(conn, customer: dict, msg, logistics_target: str) -> dict:
    """رسالة من فريقنا في مجموعة العميل: تُحفظ كسياق، وموافقتها تعتمد أي طلب معلّق"""
    chat_id = msg.chat_id.strip()
    with conn.cursor() as cur:
        record_message(cur, chat_id, msg.message_id or f"staff-{uuid.uuid4().hex[:12]}", msg.sender_phone,
                       msg.sender_name, True, msg.message_text,
                       (msg.media_mimetype or "media").split("/")[0] if getattr(msg, "media_base64", None) else None,
                       msg.quoted_message_id, msg.quoted_text)
        actions = []
        pending = get_latest_pending(cur, chat_id)
        if pending and AFFIRMATIVE_RE.match((msg.message_text or "").strip()):
            profile = load_profile(cur, customer["id"])
            actions = resolve_pending(cur, pending, True, f"staff:{msg.sender_name}", customer, profile,
                                      logistics_target, announce_in_group=False)
        conn.commit()
    return {"status": "STAFF_RECORDED", "actions": actions}


# ======================= التذكير المجدول =======================

DEFAULT_REMINDER = ("صباح الخير 🌞 نذكركم بطلب {day} ({date}).\n"
                    "Good morning, a kind reminder for {day_en}'s order ({date}).")


def recent_customer_language(cur, chat_id: str) -> str:
    """لغة العميل من آخر رسائله في المجموعة (للتذكير عند اختيار «تلقائي»)؛ فارغ = غير معروفة فيُرسل التذكير باللغتين"""
    cur.execute("""SELECT body FROM group_messages WHERE chat_id = %s AND is_staff = FALSE AND COALESCE(body, '') <> ''
                   ORDER BY created_at DESC LIMIT 10;""", (chat_id,))
    bodies = [r["body"] for r in cur.fetchall()]
    if not bodies:
        return ""
    arabic = sum(1 for b in bodies if i18n.has_arabic(b))
    return "ar" if arabic * 2 > len(bodies) else "en"


def due_reminders(cur) -> list:
    """
    التذكيرات المستحقة الآن: اليوم ضمن أيام التذكير، وتجاوزنا وقت التذكير، ولم يُرسل اليوم.
    لا يُرسل إذا كتب أحد من فريقنا في المجموعة اليوم، أو إذا وصل طلب تاريخ التسليم القادم.
    """
    now = now_local()
    today = now.date()
    cur.execute("""
        SELECT p.*, c.company_name, c.whatsapp_group_id, c.language FROM customer_order_profiles p
        JOIN customer_accounts c ON c.id = p.customer_id
        WHERE p.reminder_enabled = TRUE AND COALESCE(TRIM(c.whatsapp_group_id), '') <> ''
          AND (p.last_reminder_date IS NULL OR p.last_reminder_date < %s);
    """, (today,))
    due = []
    for p in cur.fetchall():
        days = set(range(7)) if (p.get("reminder_frequency") or "daily") == "daily" else \
            {int(d) for d in re.findall(r"\d", p["reminder_days"] or "")}
        try:
            hh, mm = [int(x) for x in (p["reminder_time"] or "09:00").split(":")[:2]]
        except Exception:
            hh, mm = 9, 0
        if today.weekday() not in days or (now.hour, now.minute) < (hh, mm):
            continue
        group = p["whatsapp_group_id"].strip()
        chat_id = group if "@" in group else f"{group}@g.us"
        delivery = today + timedelta(days=int(p["lead_days"] or 1))
        cur.execute("""
            SELECT 1 FROM order_batches b JOIN order_lines l ON l.batch_id = b.id
            WHERE b.customer_id = %s AND b.delivery_date = %s AND b.status = 'active' LIMIT 1;
        """, (p["customer_id"], delivery))
        has_order = cur.fetchone() is not None
        skip = has_order or staff_wrote_today(cur, chat_id)
        cur.execute("UPDATE customer_order_profiles SET last_reminder_date = %s WHERE customer_id = %s;",
                    (today, p["customer_id"]))
        if skip:
            logger.info(f"[Reminder] skipped for {p['company_name']} (order exists={has_order})")
            continue
        day_ar = "الغد" if delivery == today + timedelta(days=1) else WEEKDAYS_AR[delivery.weekday()]
        day_en = "tomorrow" if delivery == today + timedelta(days=1) else delivery.strftime("%A")
        lang = p.get("language") if p.get("language") in i18n.LANGS else recent_customer_language(cur, chat_id)
        template = p["reminder_text"] or (i18n.t("cu_reminder", lang) if lang else DEFAULT_REMINDER)
        text = template.format(
            day=day_ar, day_en=day_en, date=delivery.strftime("%d/%m"), company=p["company_name"])
        record_bot_message(cur, chat_id, text)
        due.append(_action(chat_id, text))
    return due


def expire_old_pending(cur):
    cur.execute("""
        UPDATE pending_order_confirmations SET status = 'expired', resolved_at = NOW()
        WHERE status = 'pending' AND created_at < NOW() - (%s || ' hours')::interval;
    """, (str(PENDING_TTL_HOURS),))
    cur.execute("DELETE FROM group_messages WHERE created_at < NOW() - INTERVAL '30 days';")

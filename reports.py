"""
reports.py - التقارير الدورية للإدارة (شهري / ربع سنوي / سنوي)

المبدأ: كل الأرقام تُحسب هنا من قاعدة البيانات (SQL + Python) وهي قابلة للتتبع.
الذكاء الاصطناعي يكتب النص فقط (الملخص التنفيذي، التفسير، التوصيات) اعتماداً على هذه الأرقام.

الأقسام: المبيعات والطلبيات، صحة قاعدة العملاء، أداء فريق المبيعات وسرعة الرد،
الجودة وملاحظات العملاء (للإنتاج والبحث والتطوير)، العمليات والمساعد الذكي.
"""

import io
import json
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from typing import Optional

import ai_client
import catalog
import order_engine

logger = logging.getLogger("SalesCRM.reports")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS management_reports (
        id SERIAL PRIMARY KEY,
        period_kind VARCHAR(10) NOT NULL,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        title VARCHAR(200) NOT NULL,
        html TEXT NOT NULL,
        metrics JSONB NOT NULL,
        source VARCHAR(20) DEFAULT 'manual',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );""",
    "CREATE INDEX IF NOT EXISTS idx_reports_period ON management_reports (period_kind, period_start);",
]

MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
KIND_AR = {"month": "شهري", "quarter": "ربع سنوي", "year": "سنوي"}
SAMPLE_STATUS_AR = {"PENDING": "قيد المتابعة", "SAMPLE_DELIVERY": "سُلّمت للشيف", "CONVERTED_PO": "تحولت لطلب",
                    "WON_CONVERTED": "تحولت لطلب", "APPROVED": "موافقة فورية", "INSTANT_APPROVAL": "موافقة فورية",
                    "NEEDS_MODIFICATION": "تحتاج تعديل", "REJECTED": "رفض", "HIGH_REJECTION": "رفض عالٍ"}
CATEGORY_AR = order_engine.CATEGORY_AR


def init_schema(run_ddl):
    for stmt in SCHEMA:
        run_ddl(stmt)


# ======================= الفترات =======================

def period_bounds(kind: str, year: int, index: int = 1) -> tuple:
    """(البداية، النهاية غير المشمولة، العنوان)"""
    if kind == "month":
        start = date(year, index, 1)
        end = date(year + (index == 12), index % 12 + 1, 1)
        label = f"{MONTHS_AR[index - 1]} {year}"
    elif kind == "quarter":
        start = date(year, 3 * (index - 1) + 1, 1)
        end = date(year + (index == 4), (3 * index) % 12 + 1, 1)
        label = f"الربع {['الأول', 'الثاني', 'الثالث', 'الرابع'][index - 1]} {year}"
    elif kind == "year":
        start, end, label = date(year, 1, 1), date(year + 1, 1, 1), f"عام {year}"
    else:
        raise ValueError("kind must be month, quarter or year")
    return start, end, label


def shift_period(kind: str, start: date, steps: int) -> tuple:
    if kind == "month":
        m = start.year * 12 + start.month - 1 + steps
        return period_bounds("month", m // 12, m % 12 + 1)
    if kind == "quarter":
        q = start.year * 4 + (start.month - 1) // 3 + steps
        return period_bounds("quarter", q // 4, q % 4 + 1)
    return period_bounds("year", start.year + steps)


def last_completed_period(kind: str, today: date) -> tuple:
    if kind == "month":
        return shift_period("month", date(today.year, today.month, 1), -1)
    if kind == "quarter":
        return shift_period("quarter", date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), -1)
    return period_bounds("year", today.year - 1)


def months_in(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


# ======================= الحسابات =======================

def _order_rows(cur, start: date, end: date) -> list:
    cur.execute("""
        SELECT b.id AS batch_id, b.delivery_date, b.customer_id, c.company_name, COALESCE(c.region, '') AS region,
               COALESCE(c.sector, '') AS sector, c.assigned_rep_id,
               l.branch_name, l.product, l.product_sku, l.quantity, l.unit, l.line_value,
               p.pieces_per_pack, p.packs_per_box, p.pieces_per_box, p.piece_weight_g, COALESCE(p.category, '') AS category
        FROM order_lines l
        JOIN order_batches b ON b.id = l.batch_id
        JOIN customer_accounts c ON c.id = b.customer_id
        LEFT JOIN products_catalog p ON p.sku = l.product_sku
        WHERE b.status = 'active' AND b.delivery_date >= %s AND b.delivery_date < %s;
    """, (start, end))
    rows = []
    for r in cur.fetchall():
        r = dict(r)
        prod = {k: r[k] for k in ("pieces_per_pack", "packs_per_box", "pieces_per_box", "piece_weight_g")} if r["product_sku"] else None
        m = catalog.line_metrics(prod, float(r["quantity"]), r["unit"])
        r["pieces"], r["kg"] = m["pieces"], m["weight_kg"]
        r["value"] = float(r["line_value"]) if r["line_value"] is not None else None
        rows.append(r)
    return rows


def _agg(rows: list, key) -> dict:
    out = defaultdict(lambda: {"pieces": 0.0, "kg": 0.0, "value": 0.0, "orders": set(), "customers": set(), "branches": set(),
                               "lines": 0, "unconverted": 0, "unpriced": 0})
    for r in rows:
        a = out[key(r)]
        a["pieces"] += r["pieces"] or 0
        a["kg"] += r["kg"] or 0
        a["value"] += r["value"] or 0
        a["orders"].add(r["batch_id"])
        a["customers"].add(r["customer_id"])
        a["branches"].add((r["customer_id"], r["branch_name"]))
        a["lines"] += 1
        a["unconverted"] += r["pieces"] is None
        a["unpriced"] += r["value"] is None
    return out


def _flat(a: dict) -> dict:
    # منتج/عميل كل أسطره بلا سعر: القيمة "غير متوفرة" (None) وليست صفراً
    value = None if a["lines"] and a["unpriced"] == a["lines"] else round(a["value"], 3)
    return {"pieces": round(a["pieces"]), "kg": round(a["kg"], 1), "value": value,
            "orders": len(a["orders"]), "customers": len(a["customers"]), "branches": len(a["branches"])}


def sales_metrics(rows: list) -> dict:
    total = _agg(rows, lambda r: "all").get("all")
    if not total:
        return {"totals": {"pieces": 0, "kg": 0, "value": 0, "orders": 0, "customers": 0, "branches": 0,
                           "lines": 0, "converted_share": None, "priced_share": None},
                "by_customer": [], "by_product": [], "by_category": [], "by_region": [], "by_weekday": [], "daily": [],
                "concentration_top3": None}
    totals = _flat(total)
    totals["value"] = totals["value"] or 0
    totals["lines"] = total["lines"]
    totals["converted_share"] = round(100 * (1 - total["unconverted"] / total["lines"]), 1)
    totals["priced_share"] = round(100 * (1 - total["unpriced"] / total["lines"]), 1)
    use_value = totals["value"] > 0
    metric = "value" if use_value else "pieces"

    names = {r["customer_id"]: r["company_name"] for r in rows}
    by_customer = sorted(({"customer_id": k, "customer": names[k], **_flat(v)} for k, v in _agg(rows, lambda r: r["customer_id"]).items()),
                         key=lambda x: -(x[metric] or 0))
    prod_names = {}
    for r in rows:
        prod_names[r["product_sku"] or r["product"]] = (r["product"], r["category"])
    by_product = sorted(({"sku": k if k in {r["product_sku"] for r in rows} else "", "product": prod_names[k][0],
                          "category": prod_names[k][1], **_flat(v)}
                         for k, v in _agg(rows, lambda r: r["product_sku"] or r["product"]).items()), key=lambda x: -(x[metric] or 0))
    by_category = sorted(({"category": k or "غير مصنف", **_flat(v)} for k, v in _agg(rows, lambda r: r["category"]).items()),
                         key=lambda x: -(x[metric] or 0))
    by_region = sorted(({"region": k or "غير محدد", **_flat(v)} for k, v in _agg(rows, lambda r: r["region"]).items()),
                       key=lambda x: -(x[metric] or 0))
    wd = _agg(rows, lambda r: r["delivery_date"].weekday())
    by_weekday = [{"weekday": WEEKDAYS_AR[i], **_flat(wd[i])} if i in wd else
                  {"weekday": WEEKDAYS_AR[i], "pieces": 0, "kg": 0, "value": 0, "orders": 0, "customers": 0, "branches": 0}
                  for i in range(7)]
    daily = sorted(({"date": k.isoformat(), **_flat(v)} for k, v in _agg(rows, lambda r: r["delivery_date"]).items()),
                   key=lambda x: x["date"])
    grand = totals[metric] or 1
    top3 = round(100 * sum((c[metric] or 0) for c in by_customer[:3]) / grand, 1)
    return {"totals": totals, "basis": metric, "by_customer": by_customer, "by_product": by_product,
            "by_category": by_category, "by_region": by_region, "by_weekday": by_weekday, "daily": daily,
            "concentration_top3": top3}


def pct_change(cur_v, prev_v) -> Optional[float]:
    if cur_v is None or prev_v in (None, 0):
        return None
    return round(100 * (cur_v - prev_v) / prev_v, 1)


def customer_health(cur, sales: dict, prev_sales: dict, start: date, end: date) -> dict:
    basis = sales.get("basis") or "pieces"
    now = {c["customer_id"]: c for c in sales["by_customer"]}
    before = {c["customer_id"]: c for c in prev_sales["by_customer"]}
    cur.execute("""
        SELECT customer_id, MIN(delivery_date) AS first_order FROM order_batches
        WHERE status = 'active' GROUP BY customer_id HAVING MIN(delivery_date) >= %s AND MIN(delivery_date) < %s;
    """, (start, end))
    new_ids = {r["customer_id"] for r in cur.fetchall()}
    lost = [{"customer": before[k]["customer"], "previous": before[k][basis]} for k in before if k not in now]
    changes = []
    for k, c in now.items():
        if k in before and before[k][basis]:
            ch = pct_change(c[basis], before[k][basis])
            changes.append({"customer": c["customer"], "current": c[basis], "previous": before[k][basis], "change_pct": ch})
    declining = sorted([x for x in changes if x["change_pct"] is not None and x["change_pct"] <= -30], key=lambda x: x["change_pct"])
    growing = sorted([x for x in changes if x["change_pct"] is not None and x["change_pct"] >= 30], key=lambda x: -x["change_pct"])

    cur.execute("""
        SELECT customer_id, ARRAY_AGG(DISTINCT delivery_date ORDER BY delivery_date) AS dates FROM order_batches
        WHERE status = 'active' AND delivery_date >= %s AND delivery_date < %s GROUP BY customer_id;
    """, (start, end))
    gaps = []
    for r in cur.fetchall():
        d = r["dates"]
        gaps += [(d[i + 1] - d[i]).days for i in range(len(d) - 1)]
    cur.execute("SELECT COUNT(*) AS n FROM customer_accounts;")
    registered = cur.fetchone()["n"]
    return {"registered": registered, "active": len(now), "active_prev": len(before),
            "new": [now[k]["customer"] for k in new_ids if k in now], "lost": sorted(lost, key=lambda x: -x["previous"]),
            "declining": declining, "growing": growing,
            "avg_days_between_orders": round(statistics.mean(gaps), 1) if gaps else None}


def responsiveness(cur, start: date, end: date) -> dict:
    """
    سرعة رد الفريق في مجموعات العملاء: لكل "دور" للعميل (رسالة لا تسبقها رسالة من العميل خلال 30 دقيقة)
    الوقت حتى أول رد من أحد أعضاء الفريق (ليس البوت) خلال 24 ساعة.
    """
    cur.execute("""
        SELECT gm.chat_id, gm.is_staff, gm.sender_name, gm.created_at, c.assigned_rep_id
        FROM group_messages gm
        LEFT JOIN customer_accounts c ON TRIM(c.whatsapp_group_id) IN (gm.chat_id, REPLACE(gm.chat_id, '@g.us', ''))
        WHERE gm.created_at >= %s AND gm.created_at < %s + INTERVAL '1 day'
        ORDER BY gm.chat_id, gm.created_at;
    """, (start, end))
    rows = cur.fetchall()
    waits, unanswered, by_rep = [], 0, defaultdict(list)
    for i, m in enumerate(rows):
        if m["is_staff"] or m["created_at"].date() >= end:
            continue
        prev = rows[i - 1] if i and rows[i - 1]["chat_id"] == m["chat_id"] else None
        if prev and not prev["is_staff"] and (m["created_at"] - prev["created_at"]) < timedelta(minutes=30):
            continue
        reply = None
        for r in rows[i + 1:]:
            if r["chat_id"] != m["chat_id"] or r["created_at"] - m["created_at"] > timedelta(hours=24):
                break
            if r["is_staff"] and r["sender_name"] != order_engine.BOT_NAME:
                reply = r
                break
        if reply:
            w = (reply["created_at"] - m["created_at"]).total_seconds() / 60
            waits.append(w)
            by_rep[m["assigned_rep_id"]].append(w)
        else:
            unanswered += 1
    summarize = lambda ws: {"turns": len(ws), "median_min": round(statistics.median(ws)) if ws else None,
                            "within_30min_pct": round(100 * sum(w <= 30 for w in ws) / len(ws), 1) if ws else None}
    return {**summarize(waits), "unanswered_24h": unanswered, "by_rep": {k: summarize(v) for k, v in by_rep.items()}}


def team_metrics(cur, sales: dict, start: date, end: date, resp: dict, health: dict) -> list:
    cur.execute("SELECT id, name, region, COALESCE(monthly_target, 0) AS monthly_target FROM sales_executives ORDER BY name;")
    reps = cur.fetchall()
    cur.execute("SELECT id, assigned_rep_id FROM customer_accounts;")
    rep_of = {r["id"]: r["assigned_rep_id"] for r in cur.fetchall()}
    months = max(1, months_in(start, end))
    out = []
    new_names = set(health["new"])
    for rep in list(reps) + [{"id": None, "name": "غير مرتبط بمندوب", "region": "", "monthly_target": 0}]:
        mine = [c for c in sales["by_customer"] if rep_of.get(c["customer_id"]) == rep["id"]]
        cur.execute("""SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE status IN ('CONVERTED_PO', 'WON_CONVERTED') OR TRIM(COALESCE(converted_po_id, '')) NOT IN ('', '—', '-')) AS conv,
                              COALESCE(SUM(po_value), 0) AS po FROM sample_deliveries
                       WHERE rep_name = %s AND delivery_date >= %s AND delivery_date < %s;""", (rep["name"], start, end))
        s = cur.fetchone()
        cur.execute("""SELECT COALESCE(SUM(amount), 0) AS total FROM expenses_log
                       WHERE (rep_id = %s OR rep_name = %s) AND created_at >= %s AND created_at < %s;""",
                    (rep["id"], rep["name"], start, end))
        exp = float(cur.fetchone()["total"])
        value = round(sum((c["value"] or 0) for c in mine), 3)
        target = float(rep["monthly_target"] or 0) * months
        assigned = sum(1 for v in rep_of.values() if v == rep["id"])
        if rep["id"] is None and not mine:
            continue
        r = resp["by_rep"].get(rep["id"], {})
        out.append({"rep": rep["name"], "region": rep["region"] or "", "assigned_customers": assigned,
                    "active_customers": len(mine), "new_customers": sum(1 for c in mine if c["customer"] in new_names),
                    "pieces": round(sum(c["pieces"] for c in mine)), "value": value, "target": round(target, 3),
                    "achievement_pct": round(100 * value / target, 1) if target else None,
                    "samples": s["n"], "samples_converted": s["conv"], "samples_po_value": float(s["po"]),
                    "expenses": round(exp, 3), "response_median_min": r.get("median_min"),
                    "response_within_30_pct": r.get("within_30min_pct")})
    return out


def quality_metrics(cur, sales: dict, start: date, end: date) -> dict:
    cur.execute("""SELECT issue_type, category, product, severity, status FROM customer_issues
                   WHERE created_at >= %s AND created_at < %s;""", (start, end))
    issues = cur.fetchall()
    complaints = [i for i in issues if i["issue_type"] == "complaint"]
    inquiries = [i for i in issues if i["issue_type"] == "inquiry"]
    by_cat = defaultdict(int)
    for c in complaints:
        by_cat[c["category"]] += 1
    pieces_by_name = {}
    for p in sales["by_product"]:
        pieces_by_name[catalog._key(p["product"])] = p["pieces"]
        if p["sku"]:
            pieces_by_name[catalog._key(p["sku"])] = p["pieces"]
    by_prod = defaultdict(lambda: {"n": 0, "high": 0})
    for c in complaints:
        k = (c["product"] or "").strip() or "غير محدد"
        by_prod[k]["n"] += 1
        by_prod[k]["high"] += c["severity"] == "high"
    prod_rows = []
    for k, v in sorted(by_prod.items(), key=lambda kv: -kv[1]["n"]):
        sold = pieces_by_name.get(catalog._key(k))
        prod_rows.append({"product": k, "complaints": v["n"], "high": v["high"], "pieces_sold": sold,
                          "per_10k_pieces": round(10000 * v["n"] / sold, 2) if sold else None})
    inq_cat = defaultdict(int)
    for i in inquiries:
        inq_cat[i["category"]] += 1
    cur.execute("""SELECT COALESCE(status, 'PENDING') AS status, COUNT(*) AS n, COALESCE(SUM(po_value), 0) AS po
                   FROM sample_deliveries WHERE delivery_date >= %s AND delivery_date < %s GROUP BY 1 ORDER BY 2 DESC;""", (start, end))
    samples = [{"status": SAMPLE_STATUS_AR.get(r["status"], r["status"]), "count": r["n"], "po_value": float(r["po"])}
               for r in cur.fetchall()]
    cur.execute("""SELECT product_name, COUNT(*) AS n,
                          COUNT(*) FILTER (WHERE status IN ('CONVERTED_PO', 'WON_CONVERTED') OR TRIM(COALESCE(converted_po_id, '')) NOT IN ('', '—', '-')) AS conv
                   FROM sample_deliveries WHERE delivery_date >= %s AND delivery_date < %s GROUP BY 1 ORDER BY 2 DESC LIMIT 10;""", (start, end))
    sample_products = [dict(r) for r in cur.fetchall()]
    return {
        "complaints_total": len(complaints), "complaints_high": sum(c["severity"] == "high" for c in complaints),
        "complaints_resolved_pct": round(100 * sum(c["status"] == "resolved" for c in complaints) / len(complaints), 1) if complaints else None,
        "complaints_by_category": [{"category": CATEGORY_AR.get(k, k), "count": v} for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])],
        "complaints_by_product": prod_rows[:15],
        "inquiries_total": len(inquiries), "inquiries_answered_by_bot": sum(i["status"] == "answered_by_bot" for i in inquiries),
        "inquiries_escalated": sum(i["status"] == "escalated" for i in inquiries),
        "inquiries_by_category": [{"category": CATEGORY_AR.get(k, k), "count": v} for k, v in sorted(inq_cat.items(), key=lambda kv: -kv[1])],
        "samples_by_status": samples, "samples_by_product": sample_products,
    }


def operations_metrics(cur, start: date, end: date) -> dict:
    cur.execute("""SELECT status, resolved_by, created_at, resolved_at, proposal->>'ambiguity' AS amb
                   FROM pending_order_confirmations WHERE created_at >= %s AND created_at < %s;""", (start, end))
    pend = cur.fetchall()
    who = defaultdict(int)
    mins = []
    for p in pend:
        if p["status"] == "approved":
            who[(p["resolved_by"] or "").split(":")[0] or "unknown"] += 1
        if p["resolved_at"]:
            mins.append((p["resolved_at"] - p["created_at"]).total_seconds() / 60)
    who_ar = {"customer": "العميل في المجموعة", "staff": "الفريق في المجموعة", "rep-link": "رابط المندوب", "dashboard": "لوحة التحكم"}
    cur.execute("""SELECT COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled, COUNT(*) AS total FROM order_batches
                   WHERE delivery_date >= %s AND delivery_date < %s;""", (start, end))
    b = cur.fetchone()
    cur.execute("""SELECT COUNT(*) FILTER (WHERE NOT is_staff) AS customer_msgs,
                          COUNT(*) FILTER (WHERE is_staff AND sender_name = %s) AS bot_msgs
                   FROM group_messages WHERE created_at >= %s AND created_at < %s;""", (order_engine.BOT_NAME, start, end))
    g = cur.fetchone()
    return {"confirmations": len(pend), "confirmations_availability": sum(p["amb"] == "availability" for p in pend),
            "confirmations_approved": sum(p["status"] == "approved" for p in pend),
            "confirmations_rejected": sum(p["status"] == "rejected" for p in pend),
            "confirmations_expired": sum(p["status"] == "expired" for p in pend),
            "approved_by": {who_ar.get(k, k): v for k, v in who.items()},
            "median_minutes_to_confirm": round(statistics.median(mins)) if mins else None,
            "orders_total": b["total"], "orders_cancelled": b["cancelled"],
            "customer_messages": g["customer_msgs"], "bot_messages": g["bot_msgs"]}


def compute(cur, kind: str, start: date, end: date) -> dict:
    prev_start, prev_end, prev_label = shift_period(kind, start, -1)
    yoy_start, yoy_end, yoy_label = (shift_period(kind, start, -12 if kind == "month" else -4 if kind == "quarter" else -1))
    cur_rows, prev_rows, yoy_rows = (_order_rows(cur, start, end), _order_rows(cur, prev_start, prev_end),
                                     _order_rows(cur, yoy_start, yoy_end))
    sales, prev_sales, yoy_sales = sales_metrics(cur_rows), sales_metrics(prev_rows), sales_metrics(yoy_rows)
    health = customer_health(cur, sales, prev_sales, start, end)
    resp = responsiveness(cur, start, end)
    t, p, y = sales["totals"], prev_sales["totals"], yoy_sales["totals"]
    kpis = {k: {"current": t[k], "previous": p[k], "vs_previous_pct": pct_change(t[k], p[k]),
                "last_year": y[k], "vs_last_year_pct": pct_change(t[k], y[k])}
            for k in ("value", "pieces", "kg", "orders", "customers", "branches")}
    prev_prod = {(x["sku"] or x["product"]): x for x in prev_sales["by_product"]}
    for x in sales["by_product"]:
        px = prev_prod.get(x["sku"] or x["product"])
        x["vs_previous_pct"] = pct_change(x["pieces"], px["pieces"]) if px else None
    prev_cust = {x["customer_id"]: x for x in prev_sales["by_customer"]}
    for x in sales["by_customer"]:
        px = prev_cust.get(x["customer_id"])
        x["vs_previous_pct"] = pct_change(x[sales.get("basis") or "pieces"], px[sales.get("basis") or "pieces"]) if px else None
    cur.execute("SELECT MIN(delivery_date) AS d FROM order_batches;")
    first = cur.fetchone()["d"]
    return {
        "period": {"kind": kind, "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
                   "previous_label": prev_label, "last_year_label": yoy_label},
        "kpis": kpis, "sales": sales, "customers": health, "responsiveness": resp,
        "team": team_metrics(cur, sales, start, end, resp, health),
        "quality": quality_metrics(cur, sales, start, end), "operations": operations_metrics(cur, start, end),
        "data_notes": {"data_since": first.isoformat() if first else None,
                       "has_last_year": bool(yoy_rows), "has_previous": bool(prev_rows),
                       "converted_share": t.get("converted_share"), "priced_share": t.get("priced_share")},
    }


# ======================= النص (Claude) =======================

NARRATIVE_TOOL = {
    "name": "write_report_text",
    "description": "Write the Arabic management-report text from the metrics.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "One-line headline of the period (Arabic)."},
            "executive_summary": {"type": "string", "description": "3-5 sentences for senior management (Arabic)."},
            "highlights": {"type": "array", "items": {"type": "string"}, "description": "Up to 3 achievements."},
            "risks": {"type": "array", "items": {"type": "string"}, "description": "Up to 3 risks / concerns."},
            "recommendations": {"type": "array", "items": {"type": "string"}, "description": "3-5 concrete, actionable recommendations naming customers/products/reps."},
            "sales_comment": {"type": "string"}, "customers_comment": {"type": "string"},
            "team_comment": {"type": "string"}, "quality_comment": {"type": "string"},
        },
        "required": ["headline", "executive_summary", "highlights", "risks", "recommendations"],
    },
}

NARRATIVE_SYSTEM = """You are the head of sales analytics at Food Development Co. (FDC), a B2B industrial bakery in Oman.
Write the text of a management report in formal, concise Modern Standard Arabic.
STRICT RULES:
- Use ONLY numbers that appear in the METRICS JSON. Do not compute new totals or invent figures. You may quote the provided percentages.
- If a comparison is null (no data for that period) say the comparison is not yet available; never guess.
- Currency is Omani Rial (ر.ع). Volumes are in pieces (حبة) and kilograms (كجم).
- Be specific: name the customers, products and reps behind each insight. No generic advice.
- If data quality notes show missing prices or unconverted units, mention it once briefly."""


def _compact(metrics: dict) -> dict:
    m = json.loads(json.dumps(metrics, default=str))
    s = m["sales"]
    for k in ("by_customer", "by_product"):
        s[k] = s[k][:12]
    s["daily"] = []
    for k in ("by_customer",):
        for x in s[k]:
            x.pop("customer_id", None)
    m["responsiveness"].pop("by_rep", None)
    return m


async def write_narrative(metrics: dict, title: str) -> dict:
    fallback = {
        "headline": title, "executive_summary": "", "highlights": [], "risks": [], "recommendations": [],
    }
    if not ai_client.is_enabled():
        return fallback
    content = [{"type": "text", "text": f"REPORT: {title}\nMETRICS JSON:\n{json.dumps(_compact(metrics), ensure_ascii=False)}"}]
    # النموذج الأقوى (نفس نموذج قراءة الصور) لأن التقرير موجّه للإدارة
    result = await ai_client.call_tool(NARRATIVE_SYSTEM, content, NARRATIVE_TOOL, use_vision=True, max_tokens=3000)
    return result or fallback


# ======================= العرض (HTML قابل للطباعة PDF) =======================

def _n(v, digits=0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


def _omr(v) -> str:
    return "—" if v is None else f"{v:,.3f} ر.ع"


def _delta(pct) -> str:
    if pct is None:
        return '<span class="delta na">لا تتوفر مقارنة</span>'
    arrow, cls = ("▲", "up") if pct > 0 else ("▼", "down") if pct < 0 else ("■", "flat")
    return f'<span class="delta {cls}">{arrow} {abs(pct):.1f}%</span>'


def _bar_chart(items: list, label_key: str, value_key: str, fmt=_n, title: str = "", max_items: int = 10) -> str:
    """رسم أعمدة أفقية (سلسلة واحدة) بـ SVG مع تلميح عند المرور على كل عمود"""
    items = [i for i in items if (i.get(value_key) or 0) > 0][:max_items]
    if not items:
        return '<p class="muted">لا توجد بيانات كافية للرسم.</p>'
    # الأسماء في عمود على اليمين، والأعمدة تمتد من خط الأساس نحو اليسار (اتجاه القراءة العربية)،
    # والقيمة بجانب نهاية العمود. اتجاه SVG ثابت LTR حتى لا تنقلب محاذاة النصوص داخل الصفحة العربية.
    w, row_h, label_w, value_w = 680, 26, 230, 96
    h = row_h * len(items) + 8
    top = max(i[value_key] for i in items) or 1
    base = w - label_w - 8
    bars = []
    for idx, i in enumerate(items):
        y = idx * row_h + 4
        bw = max(2, (base - value_w) * i[value_key] / top)
        name = str(i[label_key])
        lab = escape(name if len(name) <= 34 else name[:33] + "…")
        val = escape(fmt(i[value_key]))
        bars.append(
            f'<g class="bar"><title>{escape(name)}: {val}</title>'
            f'<text x="{w - 2}" y="{y + 16}" text-anchor="end" class="lbl">{lab}</text>'
            f'<rect x="{base - bw:.1f}" y="{y + 4}" width="{bw:.1f}" height="16" rx="4" class="mark"/>'
            f'<text x="{base - bw - 6:.1f}" y="{y + 16}" text-anchor="end" class="val">{val}</text></g>')
    return (f'<figure><figcaption>{escape(title)}</figcaption><svg viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'direction="ltr" style="direction:ltr" aria-label="{escape(title)}">'
            f'<line x1="{base}" y1="0" x2="{base}" y2="{h}" class="axis"/>{"".join(bars)}</svg></figure>')


def _column_chart(items: list, label_key: str, value_key: str, title: str) -> str:
    if not items or not any(i.get(value_key) for i in items):
        return '<p class="muted">لا توجد بيانات كافية للرسم.</p>'
    w, h, pad_b = 680, 190, 34
    n = len(items)
    slot = w / n
    top = max(i[value_key] for i in items) or 1
    cols = []
    for idx, i in enumerate(items):
        x = w - (idx + 1) * slot + slot * 0.2
        bh = (h - pad_b - 18) * i[value_key] / top
        lab, val = escape(str(i[label_key])), _n(i[value_key])
        cols.append(f'<g class="bar"><title>{lab}: {val}</title>'
                    f'<rect x="{x:.1f}" y="{h - pad_b - bh:.1f}" width="{slot * 0.6:.1f}" height="{max(bh, 0):.1f}" rx="4" class="mark"/>'
                    f'<text x="{x + slot * 0.3:.1f}" y="{h - 14}" text-anchor="middle" class="lbl">{lab}</text>'
                    + (f'<text x="{x + slot * 0.3:.1f}" y="{h - pad_b - bh - 5:.1f}" text-anchor="middle" class="val">{val}</text>' if n <= 12 else "")
                    + '</g>')
    return (f'<figure><figcaption>{escape(title)}</figcaption><svg viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'direction="ltr" style="direction:ltr" aria-label="{escape(title)}">'
            f'<line x1="0" y1="{h - pad_b}" x2="{w}" y2="{h - pad_b}" class="axis"/>{"".join(cols)}</svg></figure>')


def _table(headers: list, rows: list, empty: str = "لا توجد بيانات") -> str:
    if not rows:
        return f'<p class="muted">{escape(empty)}</p>'
    th = "".join(f"<th>{escape(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def _list(items: list, cls: str = "") -> str:
    return f'<ul class="{cls}">' + "".join(f"<li>{escape(str(i))}</li>" for i in items) + "</ul>" if items else '<p class="muted">—</p>'


CSS = """
@page { size: A4; margin: 14mm 12mm; }
:root { --brand:#3A056A; --mark:#6250d6; --ink:#1d1a22; --ink-2:#52514e; --muted:#8a8794; --line:#e7e2ef; --surface:#fcfcfb; --tint:#f6f2fb; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Tahoma, "Noto Naskh Arabic", Arial, sans-serif; color: var(--ink); background: #ecebf0; margin: 0; color-scheme: light; }
.page { max-width: 900px; margin: 18px auto; background: var(--surface); padding: 32px 38px; border-radius: 10px; box-shadow: 0 2px 14px #0001; }
header.cover { display:flex; justify-content:space-between; align-items:center; border-bottom: 3px solid var(--brand); padding-bottom: 14px; margin-bottom: 18px; }
header.cover img { height: 46px; }
h1 { font-size: 24px; color: var(--brand); margin: 0; } h2 { font-size: 18px; color: var(--brand); border-bottom: 1px solid var(--line); padding-bottom: 6px; margin: 30px 0 12px; }
h3 { font-size: 14px; color: var(--ink); margin: 18px 0 8px; }
.sub { color: var(--ink-2); font-size: 12px; margin-top: 4px; }
.headline { background: var(--tint); border-right: 4px solid var(--brand); padding: 12px 16px; border-radius: 6px; font-size: 15px; font-weight: 700; margin: 8px 0 12px; }
p, li { font-size: 13px; line-height: 1.8; }
.tiles { display:grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 12px 0; }
.tile { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
.tile .label { font-size: 11px; color: var(--ink-2); } .tile .value { font-size: 22px; font-weight: 700; margin: 2px 0; font-variant-numeric: tabular-nums; }
.tile .cmp { font-size: 10.5px; color: var(--ink-2); line-height: 1.6; }
.delta { font-weight: 700; } .delta.up { color: #0a7a2f; } .delta.down { color: #b42318; } .delta.flat, .delta.na { color: var(--muted); font-weight: 400; }
.cols { display:grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.box { border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; } .box h3 { margin-top: 2px; }
.good li::marker { content: "✓  "; color: #0a7a2f; } .bad li::marker { content: "!  "; color: #b42318; }
table { width: 100%; border-collapse: collapse; font-size: 11.5px; margin: 6px 0 10px; font-variant-numeric: tabular-nums; }
th { background: var(--tint); color: var(--brand); text-align: right; padding: 6px 7px; font-weight: 700; }
td { border-bottom: 1px solid var(--line); padding: 5px 7px; }
figure { margin: 8px 0 14px; } figcaption { font-size: 12px; color: var(--ink-2); margin-bottom: 4px; font-weight: 600; }
svg .mark { fill: var(--mark); } svg .bar:hover .mark { fill: var(--brand); } svg .lbl { font-size: 11px; fill: var(--ink); } svg .val { font-size: 10.5px; fill: var(--ink-2); }
svg .axis { stroke: var(--line); stroke-width: 1; }
.muted { color: var(--muted); font-size: 12px; } .note { font-size: 11px; color: var(--ink-2); background: #fafafa; border: 1px dashed var(--line); padding: 8px 12px; border-radius: 6px; }
.toolbar { max-width: 900px; margin: 14px auto 0; display:flex; gap:8px; justify-content:flex-start; }
.toolbar button, .toolbar a { background: var(--brand); color:#fff; border:0; padding: 8px 14px; border-radius: 8px; font-weight:700; font-size: 13px; text-decoration:none; cursor:pointer; }
section { break-inside: auto; } section.closing { break-inside: avoid; } h2 { break-after: avoid; } figure, table, .tiles, .box { break-inside: avoid; }
@media print { body { background: #fff; } .page { box-shadow:none; margin:0; max-width:none; padding: 0; } .toolbar { display:none; } h2.newpage { break-before: page; } }
"""


def render_html(metrics: dict, text: dict, title: str, report_id: Optional[int] = None, logo: str = "") -> str:
    k, s, c, t, q, o = (metrics["kpis"], metrics["sales"], metrics["customers"], metrics["team"],
                        metrics["quality"], metrics["operations"])
    r, notes, per = metrics["responsiveness"], metrics["data_notes"], metrics["period"]
    basis_value = s.get("basis") == "value"

    def tile(label, key, fmt):
        x = k[key]
        return (f'<div class="tile"><div class="label">{label}</div><div class="value">{fmt(x["current"])}</div>'
                f'<div class="cmp">مقابل {escape(per["previous_label"])}: {_delta(x["vs_previous_pct"])}<br>'
                f'مقابل {escape(per["last_year_label"])}: {_delta(x["vs_last_year_pct"])}</div></div>')

    tiles = (tile("قيمة الطلبيات", "value", _omr) + tile("الكمية (حبة)", "pieces", _n) + tile("الوزن (كجم)", "kg", lambda v: _n(v, 1))
             + tile("عدد الطلبيات", "orders", _n) + tile("العملاء النشطون", "customers", _n) + tile("الفروع المخدومة", "branches", _n))

    def comment(key):
        return f'<p>{escape(text.get(key) or "")}</p>' if text.get(key) else ""

    cust_rows = [[escape(x["customer"]), _omr(x["value"]) if basis_value else "—", _n(x["pieces"]), _n(x["kg"], 1), _n(x["orders"]),
                  _n(x["branches"]), _delta(x.get("vs_previous_pct"))] for x in s["by_customer"][:15]]
    prod_rows = [[escape(x["sku"] or "—"), escape(x["product"]), escape(x["category"] or "—"), _n(x["pieces"]), _n(x["kg"], 1),
                  _omr(x["value"]) if basis_value else "—", _n(x["customers"]), _delta(x.get("vs_previous_pct"))] for x in s["by_product"][:15]]
    metric_key = "value" if basis_value else "pieces"
    metric_fmt = _omr if basis_value else _n

    team_rows = [[escape(x["rep"]), _n(x["assigned_customers"]), _n(x["active_customers"]), _n(x["new_customers"]),
                  _n(x["pieces"]), _omr(x["value"]), _omr(x["target"]) if x["target"] else "—",
                  f'{x["achievement_pct"]:.1f}%' if x["achievement_pct"] is not None else "—",
                  f'{x["samples_converted"]}/{x["samples"]}', _omr(x["expenses"]),
                  f'{x["response_median_min"]} د' if x["response_median_min"] is not None else "—"] for x in t]

    html = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head><body>
<div class="toolbar"><button onclick="window.print()">🖨 طباعة / حفظ PDF</button>{f'<a href="/api/reports/{report_id}/excel">⬇ بيانات التقرير Excel</a>' if report_id else ''}</div>
<div class="page">
<header class="cover"><div><h1>{escape(title)}</h1>
<div class="sub">الفترة: {escape(per["start"])} — {escape(per["end"])} · أُعد في {datetime.now(order_engine.BUSINESS_TZ):%Y-%m-%d %H:%M} (توقيت مسقط)</div></div>
{f'<img src="{logo}" alt="FDC">' if logo else ''}</header>

<section><h2>الملخص التنفيذي</h2>
{f'<div class="headline">{escape(text.get("headline") or "")}</div>' if text.get("headline") else ''}
<p>{escape(text.get("executive_summary") or "")}</p>
<div class="tiles">{tiles}</div>
<div class="cols"><div class="box"><h3>أبرز الإنجازات</h3>{_list(text.get("highlights") or [], "good")}</div>
<div class="box"><h3>المخاطر والتنبيهات</h3>{_list(text.get("risks") or [], "bad")}</div></div></section>

<section><h2 class="newpage">١. المبيعات والطلبيات</h2>{comment("sales_comment")}
{_column_chart(s["daily"], "date", "pieces", "الكمية المسلّمة يومياً (حبة)") if per["kind"] == "month" else ''}
{_bar_chart(s["by_customer"], "customer", metric_key, metric_fmt, "أكبر العملاء " + ("بالقيمة" if basis_value else "بالكمية (حبة)"))}
<p class="muted">حصة أكبر 3 عملاء من الإجمالي: <b>{_n(s["concentration_top3"], 1)}%</b>{' — نسبة تركّز مرتفعة' if (s["concentration_top3"] or 0) >= 60 else ''}</p>
{_table(["العميل", "القيمة", "حبة", "كجم", "طلبيات", "فروع", "مقابل الفترة السابقة"], cust_rows)}
{_bar_chart(s["by_product"], "product", metric_key, metric_fmt, "أكثر المنتجات طلباً " + ("بالقيمة" if basis_value else "بالكمية (حبة)"))}
{_table(["الكود", "المنتج", "الفئة", "حبة", "كجم", "القيمة", "عملاء", "مقابل الفترة السابقة"], prod_rows)}
<div class="cols"><div>{_table(["الفئة", "حبة", "القيمة"], [[escape(x["category"]), _n(x["pieces"]), _omr(x["value"]) if basis_value else "—"] for x in s["by_category"]])}</div>
<div>{_table(["المنطقة", "حبة", "القيمة", "عملاء"], [[escape(x["region"]), _n(x["pieces"]), _omr(x["value"]) if basis_value else "—", _n(x["customers"])] for x in s["by_region"]])}</div></div>
{_column_chart(s["by_weekday"], "weekday", "pieces", "توزيع الكميات على أيام التسليم (حبة) — لتخطيط الإنتاج والتوصيل")}
</section>

<section><h2 class="newpage">٢. صحة قاعدة العملاء</h2>{comment("customers_comment")}
<p>العملاء المسجلون: <b>{_n(c["registered"])}</b> · النشطون في الفترة: <b>{_n(c["active"])}</b> (السابقة: {_n(c["active_prev"])}) · متوسط الأيام بين الطلبيات: <b>{_n(c["avg_days_between_orders"], 1)}</b></p>
<div class="cols"><div class="box"><h3>عملاء جدد ({len(c["new"])})</h3>{_list(c["new"], "good")}</div>
<div class="box"><h3>عملاء توقفوا عن الطلب ({len(c["lost"])})</h3>{_list([x["customer"] for x in c["lost"]], "bad")}</div></div>
<h3>عملاء متراجعون (انخفاض 30% أو أكثر عن الفترة السابقة)</h3>
{_table(["العميل", "الحالية", "السابقة", "التغير"], [[escape(x["customer"]), metric_fmt(x["current"]), metric_fmt(x["previous"]), _delta(x["change_pct"])] for x in c["declining"]], "لا يوجد عملاء متراجعون")}
<h3>عملاء متنامون (نمو 30% أو أكثر)</h3>
{_table(["العميل", "الحالية", "السابقة", "التغير"], [[escape(x["customer"]), metric_fmt(x["current"]), metric_fmt(x["previous"]), _delta(x["change_pct"])] for x in c["growing"]], "—")}
</section>

<section><h2 class="newpage">٣. أداء فريق المبيعات</h2>{comment("team_comment")}
{_table(["المندوب", "عملاء مسندون", "نشطون", "جدد", "حبة", "القيمة", "الهدف", "التحقيق", "عينات تحولت", "المصاريف", "زمن الرد"], team_rows)}
<h3>سرعة الرد على العملاء في المجموعات</h3>
<p>وسيط زمن أول رد من الفريق: <b>{f'{r["median_min"]} دقيقة' if r["median_min"] is not None else '—'}</b> · نسبة الرد خلال 30 دقيقة: <b>{f'{r["within_30min_pct"]}%' if r["within_30min_pct"] is not None else '—'}</b> · رسائل بلا رد من الفريق خلال 24 ساعة: <b>{_n(r["unanswered_24h"])}</b> (من {_n(r["turns"] + r["unanswered_24h"])} محادثة)</p>
</section>

<section><h2 class="newpage">٤. الجودة وملاحظات العملاء</h2>{comment("quality_comment")}
<p>الشكاوى: <b>{_n(q["complaints_total"])}</b> · عالية الخطورة: <b>{_n(q["complaints_high"])}</b> · نسبة المغلقة: <b>{f'{q["complaints_resolved_pct"]}%' if q["complaints_resolved_pct"] is not None else '—'}</b></p>
{_bar_chart(q["complaints_by_category"], "category", "count", _n, "الشكاوى حسب النوع")}
<h3>الشكاوى حسب المنتج (للإنتاج والبحث والتطوير)</h3>
{_table(["المنتج", "شكاوى", "عالية الخطورة", "الكمية المباعة (حبة)", "شكاوى لكل 10,000 حبة"], [[escape(x["product"]), _n(x["complaints"]), _n(x["high"]), _n(x["pieces_sold"]), _n(x["per_10k_pieces"], 2)] for x in q["complaints_by_product"]], "لا توجد شكاوى في هذه الفترة")}
<div class="cols"><div class="box"><h3>الاستفسارات</h3><p>الإجمالي: <b>{_n(q["inquiries_total"])}</b> · أجاب البوت: <b>{_n(q["inquiries_answered_by_bot"])}</b> · حُوّلت للفريق: <b>{_n(q["inquiries_escalated"])}</b></p>
{_table(["الموضوع", "العدد"], [[escape(x["category"]), _n(x["count"])] for x in q["inquiries_by_category"]], "—")}</div>
<div class="box"><h3>العينات</h3>{_table(["الحالة", "العدد", "قيمة الطلبيات الناتجة"], [[escape(x["status"]), _n(x["count"]), _omr(x["po_value"])] for x in q["samples_by_status"]], "لا توجد عينات في هذه الفترة")}
{_table(["المنتج", "عينات", "تحولت"], [[escape(x["product_name"] or "—"), _n(x["n"]), _n(x["conv"])] for x in q["samples_by_product"]], "")}</div></div>
</section>

<section><h2>٥. العمليات والمساعد الذكي</h2>
<p>الطلبيات: <b>{_n(o["orders_total"])}</b> (ملغاة: {_n(o["orders_cancelled"])}) · طلبات احتاجت تأكيداً: <b>{_n(o["confirmations"])}</b> (منها طلب تأكيد توفر: {_n(o["confirmations_availability"])}) · اعتُمدت: {_n(o["confirmations_approved"])} · رُفضت: {_n(o["confirmations_rejected"])} · انتهت دون رد: {_n(o["confirmations_expired"])} · وسيط زمن الاعتماد: {f'{o["median_minutes_to_confirm"]} دقيقة' if o["median_minutes_to_confirm"] is not None else '—'}</p>
{_table(["من اعتمد", "العدد"], [[escape(k_), _n(v)] for k_, v in o["approved_by"].items()], "—")}
<p>رسائل العملاء في المجموعات: <b>{_n(o["customer_messages"])}</b> · رسائل البوت: <b>{_n(o["bot_messages"])}</b></p>
</section>

<section class="closing"><h2>٦. التوصيات</h2>{_list(text.get("recommendations") or [])}

<p class="note">ملاحظات البيانات: الأرقام محسوبة من سجلات النظام مباشرة؛ النص التحليلي مولّد آلياً من هذه الأرقام فقط.
البيانات متوفرة منذ {escape(notes["data_since"] or "—")}.
أسطر الطلبات المحوّلة إلى حبات/كجم: {_n(notes["converted_share"], 1)}% · المسعّرة: {_n(notes["priced_share"], 1)}%.
{'' if notes["has_last_year"] else 'لا تتوفر بيانات لنفس الفترة من العام الماضي بعد. '}{'' if notes["has_previous"] else 'لا تتوفر بيانات للفترة السابقة بعد.'}</p></section>
</div></body></html>"""
    return html


def metrics_to_xlsx(metrics: dict, title: str) -> bytes:
    """بيانات التقرير في Excel (ورقة لكل قسم) لمن يريد التحليل بنفسه"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    def sheet(name, headers, rows):
        ws = wb.create_sheet(name[:31])
        ws.sheet_view.rightToLeft = True
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="3A056A")
        for r in rows:
            ws.append(r)
        for i in range(1, len(headers) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 18

    k = metrics["kpis"]
    sheet("المؤشرات", ["المؤشر", "الحالية", "السابقة", "التغير %", "العام الماضي", "التغير %"],
          [[n, k[key]["current"], k[key]["previous"], k[key]["vs_previous_pct"], k[key]["last_year"], k[key]["vs_last_year_pct"]]
           for n, key in (("القيمة ر.ع", "value"), ("حبة", "pieces"), ("كجم", "kg"), ("طلبيات", "orders"), ("عملاء", "customers"), ("فروع", "branches"))])
    s = metrics["sales"]
    sheet("العملاء", ["العميل", "القيمة", "حبة", "كجم", "طلبيات", "فروع", "التغير %"],
          [[x["customer"], x["value"], x["pieces"], x["kg"], x["orders"], x["branches"], x.get("vs_previous_pct")] for x in s["by_customer"]])
    sheet("المنتجات", ["الكود", "المنتج", "الفئة", "حبة", "كجم", "القيمة", "عملاء", "التغير %"],
          [[x["sku"], x["product"], x["category"], x["pieces"], x["kg"], x["value"], x["customers"], x.get("vs_previous_pct")] for x in s["by_product"]])
    sheet("يومي", ["التاريخ", "حبة", "كجم", "القيمة", "طلبيات"], [[x["date"], x["pieces"], x["kg"], x["value"], x["orders"]] for x in s["daily"]])
    sheet("الفريق", ["المندوب", "مسندون", "نشطون", "جدد", "حبة", "القيمة", "الهدف", "التحقيق %", "عينات", "تحولت", "المصاريف", "زمن الرد (د)"],
          [[x["rep"], x["assigned_customers"], x["active_customers"], x["new_customers"], x["pieces"], x["value"], x["target"],
            x["achievement_pct"], x["samples"], x["samples_converted"], x["expenses"], x["response_median_min"]] for x in metrics["team"]])
    q = metrics["quality"]
    sheet("الشكاوى", ["المنتج", "شكاوى", "عالية الخطورة", "حبة مباعة", "لكل 10,000 حبة"],
          [[x["product"], x["complaints"], x["high"], x["pieces_sold"], x["per_10k_pieces"]] for x in q["complaints_by_product"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def generate(conn, kind: str, start: date, end: date, label: str, source: str = "manual", logo: str = "") -> dict:
    title = f"تقرير المبيعات والعمليات {KIND_AR[kind]} — {label}"
    with conn.cursor() as cur:
        metrics = compute(cur, kind, start, end)
    conn.commit()
    text = await write_narrative(metrics, title)
    metrics["narrative"] = text
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO management_reports (period_kind, period_start, period_end, title, html, metrics, source)
                       VALUES (%s, %s, %s, %s, '', %s::jsonb, %s) RETURNING id;""",
                    (kind, start, end - timedelta(days=1), title, json.dumps(metrics, default=str), source))
        rid = cur.fetchone()["id"]
        cur.execute("UPDATE management_reports SET html = %s WHERE id = %s;",
                    (render_html(metrics, text, title, rid, logo), rid))
    conn.commit()
    return {"id": rid, "title": title, "metrics": metrics, "text": text}


def whatsapp_summary(report: dict, link: str) -> str:
    k = report["metrics"]["kpis"]
    t = report["text"]

    def line(label, key, fmt):
        x = k[key]
        d = x["vs_previous_pct"]
        return f"• {label}: {fmt(x['current'])}" + (f" ({'+' if d > 0 else ''}{d:.1f}%)" if d is not None else "")

    return (f"📊 *{report['title']}*\n" + (f"{t.get('headline')}\n" if t.get("headline") else "") + "\n"
            + "\n".join([line("القيمة", "value", _omr), line("الكمية", "pieces", lambda v: f"{v:,.0f} حبة"),
                         line("الطلبيات", "orders", lambda v: f"{v:,.0f}"), line("العملاء النشطون", "customers", lambda v: f"{v:,.0f}")])
            + (f"\n\nالتقرير الكامل:\n{link}" if link else ""))

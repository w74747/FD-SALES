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
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from typing import Optional

import ai_client
import catalog
import i18n
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


EASTERN = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def to_eastern(text: str) -> str:
    """
    الأرقام في النص الجاري بالأرقام العربية الشرقية (حسب الهوية)، مع إبقاء الأرقام الملتصقة
    بأسماء لاتينية أو مواصفات منتجات (مثل 4pcs أو 5" أو 0131) كما هي.
    """
    if not text:
        return text

    def conv(m):
        s = m.group(0)
        before = text[max(0, m.start() - 1):m.start()]
        after = text[m.end():m.end() + 1]
        if re.match(r'[A-Za-z"\'#/_\-]', before or " ") or re.match(r'[A-Za-z"\'#/_]', after or " "):
            return s
        if len(s) == 4 and s.startswith("0"):  # أكواد المنتجات
            return s
        return s.translate(EASTERN).replace(".", "٫").replace(",", "٬").replace("%", "٪")
    return re.sub(r"\d[\d,.]*%?", conv, text)


def _esc_ar(text) -> str:
    return escape(to_eastern(str(text or "")))


def _delta(pct) -> str:
    """التغير بالأرقام الغربية (جداول وأرقام): سهم + نسبة، ولون الحالة وظيفي فقط"""
    if pct is None:
        return '<span class="chg na">—</span>'
    arrow, cls = ("▲", "up") if pct > 0 else ("▼", "down") if pct < 0 else ("■", "flat")
    return f'<span class="chg {cls}">{arrow}&#8239;{abs(pct):.1f}%</span>'


def _date_ar(d) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return to_eastern(f"{d.day} {MONTHS_AR[d.month - 1]} {d.year}")


def _date_mono(d) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d.strftime("%d.%m.%Y")


def _bar_chart(items: list, label_key: str, value_key: str, fmt=_n, title: str = "", max_items: int = 10) -> str:
    """
    أعمدة أفقية (سلسلة واحدة): الأسماء على اليمين، والأعمدة تمتد من خط أساس واحد نحو اليسار،
    والقيمة بجانب نهاية العمود. بلا زوايا مستديرة (الهوية). اتجاه SVG ثابت LTR.
    """
    items = [i for i in items if (i.get(value_key) or 0) > 0][:max_items]
    if not items:
        return '<p class="empty">لا توجد بيانات كافية للرسم.</p>'
    w, row_h, label_w, value_w = 680, 24, 220, 104
    h = row_h * len(items) + 6
    top = max(i[value_key] for i in items) or 1
    base = w - label_w - 10
    bars = []
    for idx, i in enumerate(items):
        y = idx * row_h + 3
        bw = max(2, (base - value_w) * i[value_key] / top)
        name = str(i[label_key])
        lab = escape(name if len(name) <= 32 else name[:31] + "…")
        val = escape(fmt(i[value_key]))
        bars.append(
            f'<g class="bar"><title>{escape(name)}: {val}</title>'
            f'<text x="{w}" y="{y + 15}" text-anchor="end" class="lbl">{lab}</text>'
            f'<rect x="{base - bw:.1f}" y="{y + 5}" width="{bw:.1f}" height="12" class="mark"/>'
            f'<text x="{base - bw - 6:.1f}" y="{y + 15}" text-anchor="end" class="val">{val}</text></g>')
    return (f'<figure><figcaption>{escape(title)}</figcaption><svg viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'direction="ltr" style="direction:ltr" aria-label="{escape(title)}">'
            f'<line x1="{base + 0.5}" y1="0" x2="{base + 0.5}" y2="{h}" class="axis"/>{"".join(bars)}</svg></figure>')


def _column_chart(items: list, label_key: str, value_key: str, title: str) -> str:
    if not items or not any(i.get(value_key) for i in items):
        return '<p class="empty">لا توجد بيانات كافية للرسم.</p>'
    w, h, pad_b = 680, 160, 24
    n = len(items)
    slot = w / n
    bar_w = min(slot * 0.5, 40)
    top = max(i[value_key] for i in items) or 1
    cols = []
    for idx, i in enumerate(items):
        cx = w - (idx + 0.5) * slot
        bh = (h - pad_b - 18) * i[value_key] / top
        lab, val = escape(str(i[label_key])), _n(i[value_key])
        cols.append(f'<g class="bar"><title>{lab}: {val}</title>'
                    + (f'<rect x="{cx - bar_w / 2:.1f}" y="{h - pad_b - bh:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" class="mark"/>' if bh > 0 else '')
                    + f'<text x="{cx:.1f}" y="{h - 7}" text-anchor="middle" class="lbl">{lab}</text>'
                    + (f'<text x="{cx:.1f}" y="{h - pad_b - bh - 5:.1f}" text-anchor="middle" class="val">{val}</text>' if n <= 14 and i[value_key] else "")
                    + '</g>')
    return (f'<figure><figcaption>{escape(title)}</figcaption><svg viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'direction="ltr" style="direction:ltr" aria-label="{escape(title)}">'
            f'<line x1="0" y1="{h - pad_b + 0.5}" x2="{w}" y2="{h - pad_b + 0.5}" class="axis"/>{"".join(cols)}</svg></figure>')


def _table(headers: list, rows: list, empty: str = "لا توجد بيانات", num_cols: tuple = ()) -> str:
    """جداول الهوية: خط 0.75pt تحت العناوين، فواصل 0.5pt، بلا تعبئة؛ الأعمدة الرقمية بخط Mono"""
    if not rows:
        return f'<p class="empty">{escape(empty)}</p>' if empty else ""
    cls = lambda i: ' class="num"' if i in num_cols else ""
    th = "".join(f"<th{cls(i)}>{escape(h)}</th>" for i, h in enumerate(headers))
    trs = "".join("<tr>" + "".join(f"<td{cls(i)}>{c}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def _list(items: list, cls: str = "") -> str:
    return (f'<ul class="{cls}">' + "".join(f"<li>{_esc_ar(i)}</li>" for i in items) + "</ul>") if items else '<p class="empty">لا يوجد</p>'


def _band(cells: list) -> str:
    """شريط مؤشرات: الرقم بخط العرض والتسمية تحته"""
    return '<div class="band">' + "".join(
        f'<div class="cell"><div class="fig">{v}</div><div class="eyebrow">{escape(k)}</div>{extra}</div>'
        for k, v, extra in cells) + '</div>'


def _callout(title: str, body: str) -> str:
    return f'<aside class="callout"><div class="eyebrow">{escape(title)}</div><p>{body}</p></aside>'


def _face(family: str, sub: str, w: int, file: str, rng: str) -> str:
    return (f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{w};font-display:swap;"
            f"src:url(/report-fonts/{file}.woff2) format('woff2');unicode-range:{rng};}}")


AR_RANGE = "U+0600-06FF,U+0750-077F,U+0870-088E,U+0890-0891,U+0897-08E1,U+08E3-08FF,U+200C-200E,U+2010-2011,U+204F,U+2E41,U+FB50-FDFF,U+FE70-FE74,U+FE76-FEFC"
LA_RANGE = "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"

# عائلات مركّبة: الحرف العربي من الخط العربي واللاتيني من نظيره (حسب الهوية)
FONT_FACES = "".join(
    [_face("DC Text", "ar", w, f"text-ar-{w}", AR_RANGE) for w in (400, 500, 600)]
    + [_face("DC Text", "la", w, f"text-la-{w}", LA_RANGE) for w in (400, 500, 600)]
    + [_face("DC Display", "ar", w, f"disp-ar-{w}", AR_RANGE) for w in (300, 400, 700, 800)]
    + [_face("DC Display", "la", w, f"disp-la-{w}", LA_RANGE) for w in (300, 400, 600)]
    + [_face("DC Mono", "la", w, f"mono-{w}", LA_RANGE) for w in (400, 500)])

CSS = FONT_FACES + """
/* إطار الصفحة: A4، هوامش 18/20/20/20 ملم، تذييل بسطر واحد (الهوية) */
@page { size: A4; margin: 18mm 20mm 20mm;
  @bottom-right { content: "شركة تنمية الغذاء · للاستخدام الداخلي"; font: 400 7.5pt/1.4 'DC Text', sans-serif; color: #6B7688; vertical-align: top; padding-top: 3mm; }
  @bottom-center { content: "__FOOTER_MID__"; font: 400 7.5pt/1.4 'DC Text', sans-serif; color: #6B7688; vertical-align: top; padding-top: 3mm; }
  @bottom-left { content: "\\202A" counter(page) " / " counter(pages) "\\202C"; font: 400 7.5pt/1.4 'DC Mono', monospace; color: #6B7688; vertical-align: top; padding-top: 3mm; } }
@page :first { @bottom-right { content: none; } @bottom-center { content: none; } @bottom-left { content: none; } }

:root {
  --ink:#0B1020; --steel:#2F4368; --graphite:#414C61; --mist:#6B7688;
  --canvas:#F6F8FB; --mesa:#EDF1F7; --frost:#DCE2EC; --frost-deep:#C3CCDA; --paper:#FFFFFF;
  --accent:#3A056A;   /* لون FDC يحل محل Castle Blue كلون الختم المؤسسي */
  --mark:#6250d6;     /* لون الرسوم البيانية (تم التحقق منه) */
  --ok:#0F7A5A; --warn:#B0731A; --crit:#C0342B;
  --hair:.5pt; --rule:.75pt; --emph:1.5pt;
}
* { box-sizing: border-box; border-radius: 0; }
html { color-scheme: light; }
body { margin: 0; background: var(--canvas); color: var(--ink);
       font: 400 10pt/1.9 'DC Text', Tahoma, sans-serif; text-align: right; }
.sheet { width: 210mm; max-width: 100%; margin: 14px auto 28px; background: var(--paper); padding: 18mm 20mm 20mm; }

/* شريط أدوات الشاشة (لا يُطبع) */
.toolbar { width: 210mm; max-width: 100%; margin: 14px auto 0; display: flex; gap: 6px; }
.toolbar button, .toolbar a { font: 500 9pt 'DC Text', sans-serif; padding: 2mm 4mm; border: var(--rule) solid var(--ink); background: var(--ink); color: #fff; text-decoration: none; cursor: pointer; }
.toolbar a { background: var(--paper); color: var(--ink); }
.toolbar :focus-visible { outline: 2px solid var(--mark); outline-offset: 2px; }

/* تسميات صغيرة: في العربية بلا تباعد ولا أحرف كبيرة، Almarai 700 بلون رمادي */
.eyebrow { font: 700 7.5pt/1.5 'DC Display', sans-serif; color: var(--mist); letter-spacing: 0; }
.figure, .num, .mono { font-family: 'DC Mono', monospace; font-variant-numeric: tabular-nums; }

/* الترويسة */
.docheader { display: flex; align-items: center; justify-content: space-between; height: 14mm; border-bottom: var(--hair) solid var(--accent); margin-bottom: 14mm; }
.docheader img { height: 11mm; }
.docheader .eyebrow { color: var(--mist); }

/* الغلاف: سطر التصنيف، عنوان هو جملة الحجة، المقدمة، شريط البيانات */
.cover .eyebrow.class { color: var(--accent); margin-bottom: 4mm; }
.cover h1 { font: 400 20pt/1.45 'DC Display', sans-serif; letter-spacing: -0.01em; margin: 0 0 6mm; max-width: 150mm; color: var(--ink); }
.lede { font: 400 10.5pt/1.85 'DC Text', sans-serif; color: var(--graphite); max-width: 140mm; margin: 0 0 9mm; }
.rail { display: grid; grid-template-columns: repeat(4, 1fr); column-gap: 5mm; border-top: var(--hair) solid var(--frost); border-bottom: var(--hair) solid var(--frost); padding: 4mm 0; margin-bottom: 9mm; }
.rail .value { font: 500 8.5pt/1.6 'DC Mono', monospace; color: var(--ink); margin-top: 1mm; }
.rail .value.text { font: 500 9pt/1.6 'DC Text', sans-serif; }

/* شريط المؤشرات: خلايا بفواصل رفيعة */
.band { display: grid; grid-template-columns: repeat(3, 1fr); border: var(--hair) solid var(--frost-deep); margin: 0 0 8mm; }
.band .cell { padding: 5mm 5mm 4mm; border-inline-start: var(--hair) solid var(--frost-deep); border-top: var(--hair) solid var(--frost-deep); }
.band .cell:nth-child(3n+1) { border-inline-start: 0; } .band .cell:nth-child(-n+3) { border-top: 0; }
.band .fig { font: 300 16pt/1.25 'DC Display', sans-serif; color: var(--ink); margin-bottom: 1.5mm; white-space: nowrap; }
.band .cmp { display: flex; justify-content: space-between; gap: 2mm; font-size: 7.5pt; line-height: 1.9; color: var(--mist); }
.band.small { grid-template-columns: repeat(4, 1fr); } .band.small .cell:nth-child(3n+1) { border-inline-start: var(--hair) solid var(--frost-deep); }
.band.small .cell:nth-child(4n+1) { border-inline-start: 0; } .band.small .cell { border-top: 0; } .band.small .fig { font-size: 13pt; }
.chg { font: 500 7.5pt 'DC Mono', monospace; white-space: nowrap; }
.chg.up { color: var(--ok); } .chg.down { color: var(--crit); } .chg.flat, .chg.na { color: var(--mist); }

/* الأقسام: رقم القسم كتسمية صغيرة، عنوان Almarai، ثم جملة الخلاصة */
section { margin-top: 12mm; }
.secno { color: var(--accent); margin-bottom: 1mm; }
h2 { font: 400 14pt/1.5 'DC Display', sans-serif; letter-spacing: -0.01em; color: var(--ink); margin: 0 0 3mm; }
h3 { font: 600 10.5pt/1.5 'DC Text', sans-serif; color: var(--ink); margin: 7mm 0 2mm; }
p { margin: 0 0 3mm; max-width: 140mm; }
.thesis { font-size: 10.5pt; line-height: 1.85; color: var(--graphite); margin-bottom: 6mm; }

/* كتلة التنبيه: واحدة لكل قسم كحد أقصى */
.callout { background: var(--mesa); border-inline-start: var(--emph) solid var(--accent); padding: 5mm 6mm; margin: 6mm 0; break-inside: avoid; }
.callout .eyebrow { color: var(--ink); margin-bottom: 1.5mm; }
.callout p { margin: 0; max-width: none; font-size: 10pt; }

.twocol { display: grid; grid-template-columns: 1fr 1fr; column-gap: 10mm; }
ul { margin: 0; padding-inline-start: 5mm; } li { margin-bottom: 1mm; }
ul.good li::marker { content: "+  "; color: var(--ok); font-weight: 600; }
ul.bad li::marker { content: "!  "; color: var(--crit); font-weight: 600; }
ol.recs { margin: 0; padding: 0; list-style: none; counter-reset: rec; }
ol.recs li { counter-increment: rec; display: grid; grid-template-columns: 10mm 1fr; padding: 3mm 0; border-bottom: var(--hair) solid var(--frost); margin: 0; }
ol.recs li::before { content: counter(rec, arabic-indic); font: 300 12pt/1.5 'DC Display', sans-serif; color: var(--accent); }

/* الجداول: بلا تعبئة ولا إطار خارجي */
table { width: 100%; border-collapse: collapse; font: 400 8.5pt/1.65 'DC Text', sans-serif; margin: 2mm 0 7mm; }
th { font: 700 7.5pt/1.5 'DC Display', sans-serif; color: var(--mist); text-align: right; padding: 0 0 2mm 3mm; border-bottom: var(--rule) solid var(--ink); white-space: nowrap; }
td { padding: 2.2mm 0 2.2mm 3mm; border-bottom: var(--hair) solid var(--frost); vertical-align: top; }
th.num, td.num { text-align: left; white-space: nowrap; font-family: 'DC Mono', monospace; font-size: 8pt; }
th.num { font: 700 7.5pt/1.5 'DC Display', sans-serif; }

/* الرسوم */
figure { margin: 3mm 0 6mm; }
figcaption { margin-bottom: 2mm; font: 700 7.5pt/1.5 'DC Display', sans-serif; color: var(--mist); }
svg text { font-family: 'DC Text', sans-serif; }
svg .mark { fill: var(--mark); } svg .bar:hover .mark { fill: var(--accent); }
svg .lbl { font-size: 11px; fill: var(--ink); } svg .val { font-family: 'DC Mono', monospace; font-size: 10px; fill: var(--graphite); }
svg .axis { stroke: var(--frost-deep); stroke-width: 1; }

.empty { color: var(--mist); font-size: 9pt; }
.disclaimer { font: 400 7.5pt/1.8 'DC Text', sans-serif; color: var(--mist); max-width: none; border-top: var(--hair) solid var(--frost); padding-top: 3mm; margin-top: 10mm; }

/* الطباعة */
h2, h3, .secno, figcaption, .sechead, .thesis { break-after: avoid; }
.sechead { break-inside: avoid; }
/* رأس القسم لا يبقى وحيداً أسفل الصفحة: يُحجز له مع أول محتوى ارتفاع كافٍ */
.sechead::after { content: ""; display: block; height: 0; }
section:not(.newpage) { break-before: auto; }
section > .thesis { break-inside: avoid; }
figure, table, .band, .twocol, .rail, .callout { break-inside: avoid; }
section.closing { break-inside: avoid; }
@media print {
  body { background: #fff; }
  .sheet { margin: 0; padding: 0; width: auto; }
  .toolbar { display: none; }
  section.newpage { break-before: page; margin-top: 0; }
}
@media screen and (max-width: 760px) {
  .sheet { padding: 10mm 6mm; } .band, .band.small { grid-template-columns: 1fr 1fr; } .twocol, .rail { grid-template-columns: 1fr 1fr; }
  .cover h1 { font-size: 17pt; } table { display: block; overflow-x: auto; }
}
"""

KIND_TITLE = {"month": "التقرير الشهري للمبيعات والعمليات", "quarter": "التقرير الربع سنوي للمبيعات والعمليات",
              "year": "التقرير السنوي للمبيعات والعمليات"}


def render_html(metrics: dict, text: dict, title: str, report_id: Optional[int] = None, logo: str = "") -> str:
    k, s, c, t, q, o = (metrics["kpis"], metrics["sales"], metrics["customers"], metrics["team"],
                        metrics["quality"], metrics["operations"])
    r, notes, per = metrics["responsiveness"], metrics["data_notes"], metrics["period"]
    basis_value = s.get("basis") == "value"
    metric_key = "value" if basis_value else "pieces"
    metric_fmt = _omr if basis_value else _n
    pct = lambda v: f"{v:.1f}%" if v is not None else "—"
    period_name = per.get("label") or title
    ref = f"FDC-RPT-{per['start'][:7].replace('-', '')}-{per['kind'][0].upper()}{report_id or ''}"

    def kpi(label, key, fmt):
        x = k[key]
        return (label, fmt(x["current"]),
                f'<div class="cmp"><span>عن {escape(per["previous_label"])}</span>{_delta(x["vs_previous_pct"])}</div>'
                f'<div class="cmp"><span>عن {escape(per["last_year_label"])}</span>{_delta(x["vs_last_year_pct"])}</div>')

    def small(pairs):
        return _band([(lbl, val, "") for lbl, val in pairs]).replace('class="band"', 'class="band small"', 1)

    def thesis(key):
        return f'<p class="thesis">{_esc_ar(text.get(key))}</p>' if text.get(key) else ""

    def section(no, name, body, newpage=False):
        # جملة الخلاصة تبقى مع العنوان في كتلة واحدة لا تنقسم
        lead = ""
        if body.startswith('<p class="thesis">'):
            cut = body.index("</p>") + 4
            lead, body = body[:cut], body[cut:]
        return (f'<section class="{"newpage" if newpage else ""}"><div class="sechead"><div class="eyebrow secno">القسم {to_eastern(f"{no:02d}")}</div>'
                f'<h2>{escape(name)}</h2>{lead}</div>{body}</section>')

    value_cell = lambda x: _omr(x["value"]) if basis_value else "—"
    cust_rows = [[escape(x["customer"]), value_cell(x), _n(x["pieces"]), _n(x["kg"], 1), _n(x["orders"]), _n(x["branches"]),
                  _delta(x.get("vs_previous_pct"))] for x in s["by_customer"][:15]]
    prod_rows = [[escape(x["product"]), escape(x["sku"] or "—"), escape(x["category"] or "—"), _n(x["pieces"]), _n(x["kg"], 1),
                  value_cell(x), _n(x["customers"]), _delta(x.get("vs_previous_pct"))] for x in s["by_product"][:15]]
    team_rows = [[escape(x["rep"]), _n(x["assigned_customers"]), _n(x["active_customers"]), _n(x["new_customers"]), _n(x["pieces"]),
                  _omr(x["value"]), _omr(x["target"]) if x["target"] else "—", pct(x["achievement_pct"]),
                  f'{x["samples_converted"]}/{x["samples"]}', _omr(x["expenses"]),
                  f'{x["response_median_min"]} د' if x["response_median_min"] is not None else "—"] for x in t]
    change_rows = lambda lst: [[escape(x["customer"]), metric_fmt(x["current"]), metric_fmt(x["previous"]), _delta(x["change_pct"])] for x in lst]
    conc = s["concentration_top3"]

    # كتلة تنبيه واحدة لكل قسم (من الأرقام المحسوبة، دون نص مولّد)
    top3 = "، ".join(x["customer"] for x in s["by_customer"][:3])
    sales_call = (_callout("تركّز الطلب", f'يمثل أكبر ثلاثة عملاء ({escape(top3)}) <b>{to_eastern(pct(conc))}</b> من '
                           + ("قيمة الطلبيات" if basis_value else "الكمية") + '. أي تراجع لأحدهم ينعكس مباشرة على الإجمالي.')
                  if conc is not None and conc >= 60 and len(s["by_customer"]) > 3 else "")
    watch = [x["customer"] for x in c["lost"]] + [x["customer"] for x in c["declining"]]
    cust_call = _callout("عملاء يحتاجون زيارة هذا الشهر", escape("، ".join(watch))) if watch else ""
    high = [x for x in q["complaints_by_product"] if x["high"]]
    qual_call = (_callout("شكاوى عالية الخطورة", "، ".join(f'{escape(x["product"])} ({to_eastern(_n(x["high"]))})' for x in high)
                          + "، تحتاج متابعة فورية مع الإنتاج.").replace("(", "(").replace(")", ")") if high else "")
    team_call = (_callout("رسائل عملاء بلا رد",
                          f'<b>{to_eastern(_n(r["unanswered_24h"]))}</b> محادثة لم يرد عليها أحد من الفريق خلال {to_eastern("24")} ساعة.')
                 if r["unanswered_24h"] else "")

    sales_body = (thesis("sales_comment")
                  + (_column_chart(s["daily"], "date", "pieces", "الكمية المسلّمة في كل يوم تسليم · حبة") if per["kind"] == "month" else "")
                  + _bar_chart(s["by_customer"], "customer", metric_key, metric_fmt, "أكبر العملاء · " + ("قيمة الطلبيات" if basis_value else "الكمية بالحبة"))
                  + sales_call
                  + _table(["العميل", "القيمة", "حبة", "كجم", "طلبيات", "فروع", "عن الفترة السابقة"], cust_rows, num_cols=(1, 2, 3, 4, 5, 6))
                  + _bar_chart(s["by_product"], "product", metric_key, metric_fmt, "أكثر المنتجات طلباً · " + ("القيمة" if basis_value else "الكمية بالحبة"))
                  + _table(["المنتج", "الكود", "الفئة", "حبة", "كجم", "القيمة", "عملاء", "عن الفترة السابقة"], prod_rows, num_cols=(1, 3, 4, 5, 6, 7))
                  + '<div class="twocol"><div><h3>حسب فئة المنتج</h3>'
                  + _table(["الفئة", "حبة", "القيمة"], [[escape(x["category"]), _n(x["pieces"]), value_cell(x)] for x in s["by_category"]], num_cols=(1, 2))
                  + '</div><div><h3>حسب المنطقة</h3>'
                  + _table(["المنطقة", "حبة", "القيمة", "عملاء"], [[escape(x["region"]), _n(x["pieces"]), value_cell(x), _n(x["customers"])] for x in s["by_region"]], num_cols=(1, 2, 3))
                  + '</div></div>'
                  + _column_chart(s["by_weekday"], "weekday", "pieces", "توزيع الكمية على أيام التسليم · حبة · لتخطيط الإنتاج والتوصيل"))

    cust_body = (thesis("customers_comment")
                 + small([("العملاء المسجلون", _n(c["registered"])), ("النشطون في الفترة", _n(c["active"])),
                          ("النشطون في الفترة السابقة", _n(c["active_prev"])), ("متوسط الأيام بين الطلبيات", _n(c["avg_days_between_orders"], 1))])
                 + cust_call
                 + f'<div class="twocol"><div><h3>عملاء جدد</h3>{_list(c["new"], "good")}</div>'
                 + f'<div><h3>عملاء توقفوا عن الطلب</h3>{_list([x["customer"] for x in c["lost"]], "bad")}</div></div>'
                 + '<h3>عملاء متراجعون بنسبة 30% أو أكثر</h3>'.replace("30%", to_eastern("30%"))
                 + _table(["العميل", "الفترة الحالية", "الفترة السابقة", "التغير"], change_rows(c["declining"]), "لا يوجد عملاء متراجعون", num_cols=(1, 2, 3))
                 + '<h3>عملاء متنامون بنسبة 30% أو أكثر</h3>'.replace("30%", to_eastern("30%"))
                 + _table(["العميل", "الفترة الحالية", "الفترة السابقة", "التغير"], change_rows(c["growing"]), "لا يوجد", num_cols=(1, 2, 3)))

    team_body = (thesis("team_comment")
                 + _table(["المندوب", "مسندون", "نشطون", "جدد", "حبة", "القيمة", "الهدف", "التحقيق", "عينات", "المصاريف", "زمن الرد"], team_rows, num_cols=tuple(range(1, 11)))
                 + '<h3>سرعة الرد على العملاء في المجموعات</h3>'
                 + small([("وسيط زمن أول رد", f'{r["median_min"]} د' if r["median_min"] is not None else "—"),
                          ("الرد خلال 30 دقيقة", pct(r["within_30min_pct"])),
                          ("محادثات بلا رد خلال 24 ساعة", _n(r["unanswered_24h"])),
                          ("إجمالي المحادثات", _n(r["turns"] + r["unanswered_24h"]))])
                 + team_call)

    qual_body = (thesis("quality_comment")
                 + small([("الشكاوى", _n(q["complaints_total"])), ("عالية الخطورة", _n(q["complaints_high"])),
                          ("نسبة المغلقة", pct(q["complaints_resolved_pct"])), ("الاستفسارات", _n(q["inquiries_total"]))])
                 + qual_call
                 + _bar_chart(q["complaints_by_category"], "category", "count", _n, "الشكاوى حسب النوع")
                 + '<h3>الشكاوى حسب المنتج، للإنتاج والبحث والتطوير</h3>'
                 + _table(["المنتج", "شكاوى", "عالية الخطورة", "المباع · حبة", "لكل 10,000 حبة"], [[escape(x["product"]), _n(x["complaints"]), _n(x["high"]), _n(x["pieces_sold"]), _n(x["per_10k_pieces"], 2)] for x in q["complaints_by_product"]], "لا توجد شكاوى في هذه الفترة", num_cols=(1, 2, 3, 4))
                 + '<div class="twocol"><div><h3>الاستفسارات</h3>'
                 + _table(["الموضوع", "العدد"], [[escape(x["category"]), _n(x["count"])] for x in q["inquiries_by_category"]]
                          + [["<span class='eyebrow'>أجاب البوت</span>", _n(q["inquiries_answered_by_bot"])], ["<span class='eyebrow'>حُوّلت للفريق</span>", _n(q["inquiries_escalated"])]], "", num_cols=(1,))
                 + '</div><div><h3>العينات</h3>'
                 + _table(["الحالة", "العدد", "قيمة الطلبيات"], [[escape(x["status"]), _n(x["count"]), _omr(x["po_value"])] for x in q["samples_by_status"]], "لا توجد عينات في هذه الفترة", num_cols=(1, 2))
                 + _table(["المنتج", "عينات", "تحولت"], [[escape(x["product_name"] or "—"), _n(x["n"]), _n(x["conv"])] for x in q["samples_by_product"]], "", num_cols=(1, 2))
                 + '</div></div>')

    ops_body = (small([("الطلبيات", _n(o["orders_total"])), ("الملغاة", _n(o["orders_cancelled"])),
                       ("احتاجت تأكيداً", _n(o["confirmations"])), ("وسيط زمن الاعتماد", f'{o["median_minutes_to_confirm"]} د' if o["median_minutes_to_confirm"] is not None else "—")])
                + _table(["المؤشر", "العدد"], [["اعتُمدت", _n(o["confirmations_approved"])], ["رُفضت", _n(o["confirmations_rejected"])],
                                               ["انتهت دون رد", _n(o["confirmations_expired"])], ["طلبات تأكيد توفر", _n(o["confirmations_availability"])],
                                               ["رسائل العملاء في المجموعات", _n(o["customer_messages"])], ["رسائل البوت", _n(o["bot_messages"])]]
                         + [[f"اعتمدها: {escape(k_)}", _n(v)] for k_, v in o["approved_by"].items()], "", num_cols=(1,)))

    recs = text.get("recommendations") or []
    footer_mid = f'{KIND_TITLE.get(per["kind"], "")} · {period_name}'.replace('"', "")

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS.replace("__FOOTER_MID__", footer_mid.replace(chr(92), "").replace('"', ""))}</style></head><body>
<nav class="toolbar"><button onclick="window.print()">طباعة أو حفظ PDF</button>{f'<a href="/api/reports/{report_id}/excel">تنزيل البيانات Excel</a>' if report_id else ''}</nav>
<main class="sheet">
<header class="docheader">{f'<img src="{logo}" alt="شركة تنمية الغذاء">' if logo else '<b>شركة تنمية الغذاء</b>'}<span class="eyebrow">تقرير الإدارة · للاستخدام الداخلي</span></header>

<div class="cover">
  <div class="eyebrow class">{escape(KIND_TITLE.get(per["kind"], title))} · {escape(period_name)}</div>
  <h1>{_esc_ar(text.get("headline") or period_name)}</h1>
  {f'<p class="lede">{_esc_ar(text.get("executive_summary"))}</p>' if text.get("executive_summary") else ''}
  <div class="rail">
    <div><div class="eyebrow">الفترة</div><div class="value" dir="ltr" style="text-align:right">{(date.fromisoformat(per["start"]).strftime("%d.%m") if per["start"][:4] == per["end"][:4] else _date_mono(per["start"]))} – {_date_mono(per["end"])}</div></div>
    <div><div class="eyebrow">تاريخ الإصدار</div><div class="value">{datetime.now(order_engine.BUSINESS_TZ):%d.%m.%Y}</div></div>
    <div><div class="eyebrow">مُعدّ لـ</div><div class="value text">الإدارة العليا</div></div>
    <div><div class="eyebrow">المرجع</div><div class="value">{escape(ref)}</div></div>
  </div>
  {_band([kpi("قيمة الطلبيات · ر.ع", "value", lambda v: f"{v:,.3f}"), kpi("الكمية · حبة", "pieces", _n), kpi("الوزن · كجم", "kg", lambda v: _n(v, 1)),
          kpi("عدد الطلبيات", "orders", _n), kpi("العملاء النشطون", "customers", _n), kpi("الفروع المخدومة", "branches", _n)])}
  <div class="twocol"><div><h3>أبرز الإنجازات</h3>{_list(text.get("highlights") or [], "good")}</div>
  <div><h3>المخاطر والتنبيهات</h3>{_list(text.get("risks") or [], "bad")}</div></div>
</div>

{section(1, "المبيعات والطلبيات", sales_body, newpage=True)}
{section(2, "صحة قاعدة العملاء", cust_body)}
{section(3, "أداء فريق المبيعات", team_body)}
{section(4, "الجودة وملاحظات العملاء", qual_body)}
{section(5, "العمليات والمساعد الذكي", ops_body)}

<section class="closing"><div class="sechead"><div class="eyebrow secno">القسم {to_eastern("06")}</div><h2>التوصيات والخطوات التالية</h2></div>
  {('<ol class="recs">' + "".join(f"<li><span>{_esc_ar(x)}</span></li>" for x in recs) + '</ol>') if recs else '<p class="empty">لا توجد توصيات.</p>'}
  <p class="disclaimer">الأرقام محسوبة مباشرة من سجلات النظام، والنص التحليلي مكتوب آلياً اعتماداً على هذه الأرقام فقط.
  البيانات متوفرة منذ {_date_ar(notes["data_since"]) if notes["data_since"] else "—"}.
  نسبة أسطر الطلبات المحوّلة إلى حبات وكيلوجرام {to_eastern(pct(notes["converted_share"]))}، والمسعّرة {to_eastern(pct(notes["priced_share"]))}.
  {'' if notes["has_last_year"] else 'لا تتوفر بعد بيانات لنفس الفترة من العام الماضي. '}{'' if notes["has_previous"] else 'لا تتوفر بعد بيانات للفترة السابقة.'}</p>
</section>
</main></body></html>"""


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
    title = f"{KIND_TITLE[kind]} — {label}"
    with conn.cursor() as cur:
        metrics = compute(cur, kind, start, end)
    conn.commit()
    text = await write_narrative(metrics, title)
    metrics["narrative"] = text
    metrics["period"]["label"] = label
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO management_reports (period_kind, period_start, period_end, title, html, metrics, source)
                       VALUES (%s, %s, %s, %s, '', %s::jsonb, %s) RETURNING id;""",
                    (kind, start, end - timedelta(days=1), title, json.dumps(metrics, default=str), source))
        rid = cur.fetchone()["id"]
        cur.execute("UPDATE management_reports SET html = %s WHERE id = %s;",
                    (render_html(metrics, text, title, rid, logo), rid))
    conn.commit()
    return {"id": rid, "title": title, "metrics": metrics, "text": text}


def _en_label(period: dict) -> str:
    start = date.fromisoformat(period["start"])
    if period["kind"] == "month":
        return start.strftime("%B %Y")
    if period["kind"] == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    return str(start.year)


def whatsapp_summary(report: dict, link: str, lang: str = "en") -> str:
    """ملخص التقرير على الواتساب بلغة مسؤول النظام (التقرير الكامل نفسه بالعربية)"""
    lang = i18n.norm(lang)
    k = report["metrics"]["kpis"]
    t = report["text"]
    T = lambda key, **kw: i18n.t(key, lang, **kw)

    def line(label, key, fmt):
        x = k[key]
        d = x["vs_previous_pct"]
        return f"• {label}: {fmt(x['current'])}" + (f" ({'+' if d > 0 else ''}{d:.1f}%)" if d is not None else "")

    if lang == "ar":
        title, headline = report["title"], t.get("headline")
    else:
        title = T("rp_title_" + report["metrics"]["period"]["kind"], label=_en_label(report["metrics"]["period"]))
        headline = None  # السرد مكتوب بالعربية في التقرير
    value_fmt = _omr if lang == "ar" else (lambda v: f"{v:,.3f} OMR")
    return (f"📊 *{title}*\n" + (f"{headline}\n" if headline else "") + "\n"
            + "\n".join([line(T("rp_value"), "value", value_fmt),
                         line(T("rp_pieces"), "pieces", lambda v: f"{v:,.0f} {T('rp_pieces_unit')}"),
                         line(T("rp_orders"), "orders", lambda v: f"{v:,.0f}"),
                         line(T("rp_customers"), "customers", lambda v: f"{v:,.0f}")])
            + (f"\n\n{T('rp_full')}\n{link}" if link else ""))

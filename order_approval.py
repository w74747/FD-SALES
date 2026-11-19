"""
مسار اعتماد الطلبات قبل وصولها للوجستيك.

الدورة:
1. العميل يرسل الطلب في مجموعته ← المحرك الذكي يفهمه (order_engine).
2. البوت يرسل تفاصيل الطلب لموظف إدخال البيانات (مجموعة أو رقم) بمرجع FD-رقم.
3. الموظف يُدخل الطلب في نظام الشركة ويردّ على رسالة البوت برقم الطلب (مرحلة "مُدخل").
4. بعد اعتماد قسم المالية يردّ الموظف على نفس الرسالة بـ «معتمد» (أو الرقم والاعتماد معاً).
   الاعتماد قد لا يكون بالترتيب: الرد على رسالة قديمة يُطابق طلبها عبر المرجع FD-رقم في الرسالة المقتبسة.
5. البوت يرسل الطلب لمسؤول اللوجستيك مع رقم الطلب والحالة (معتمد).

التعديلات والإضافات والإلغاءات بعد الإرسال تمر بنفس المسار كرسالة «تحديث» مرتبطة برقم الطلب نفسه.
استثناء للسلامة: إلغاء طلب وصل اللوجستيك فعلاً يُبلَّغ للوجستيك فوراً بإيقاف التجهيز، ثم يُؤكَّد من إدخال البيانات.
"""
import json
import logging
import re
from typing import Optional

import order_engine as oe

logger = logging.getLogger("fd-sales.order-approval")

REF_RE = re.compile(r"\bFD-(\d{1,9})\b", re.IGNORECASE)
# الرفض يُفحص قبل الاعتماد حتى لا تُفهم «غير معتمد» كاعتماد
REJECT_RE = re.compile(r"غير\s*معتمد|لم\s*يعتمد|لم\s*تعتمد|مرفوض|رفض|\breject(ed)?\b|\bdeclined?\b|\bnot\s+approved\b|❌", re.IGNORECASE)
APPROVE_RE = re.compile(r"معتمد|اعتمد|تم\s*الاعتماد|موافق(ة)?\s*المالية|\bapproved?\b|\bconfirmed\b|✅|👍", re.IGNORECASE)
# تأكيد تنفيذ الإلغاء في النظام
DONE_RE = re.compile(r"\bتم\b|ملغي|ملغى|الغي|ألغي|الغاء|إلغاء|\bdone\b|\bok(ay)?\b|\bcancel(l?ed)?\b", re.IGNORECASE)
# رقم الطلب في نظام الشركة: أرقام (3 فأكثر) مع بادئة حروف اختيارية، مثل 10234 أو SO-2026-00123
ERP_RE = re.compile(r"(?<![\w/.:-])((?:[A-Za-z]{1,6}[-/#]?)?\d{3,}(?:[-/]\d+)*)(?![\w/.:])")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 2)

OPEN_STATUSES = ("awaiting", "entered")
KIND_AR = {"new": "طلب جديد", "update": "تحديث طلب", "cancel": "إلغاء طلب"}
STATUS_AR = {"awaiting": "بانتظار الإدخال", "entered": "مُدخل — بانتظار المالية", "approved": "معتمد ومرسل للوجستيك",
             "rejected": "مرفوض", "superseded": "استُبدل بتحديث أحدث"}


def _digits(v: str) -> str:
    return re.sub(r"[^0-9]", "", v or "")


def _key(lines: list) -> list:
    """مقارنة الأسطر بالفرع والصنف والكمية والوحدة فقط (السعر لا يغيّر الطلب)"""
    return sorted((l["branch"], l["product"], round(float(l["quantity"]), 3), l["unit"]) for l in lines or [])


def is_data_entry_chat(target: str, chat_id: str, sender_phone: str) -> bool:
    """هل الرسالة قادمة من محادثة إدخال البيانات؟ (مجموعة: نفس المعرّف، رقم: محادثة خاصة من نفس الرقم)"""
    t = _digits(target)
    if not t:
        return False
    is_group_target = target.strip().endswith("@g.us") or "-" in target or len(t) > 15
    if is_group_target:
        return chat_id.endswith("@g.us") and _digits(chat_id) == t
    if chat_id.endswith("@g.us"):
        return False
    tail = t[-8:]
    return _digits(chat_id).endswith(tail) or _digits(sender_phone).endswith(tail)


def _customer(cur, customer_id: int) -> dict:
    cur.execute("SELECT id, company_name, brand_name, assigned_rep_id FROM customer_accounts WHERE id = %s;", (customer_id,))
    return dict(cur.fetchone())


def _instructions(kind: str) -> str:
    if kind == "cancel":
        return "↩️ ألغِ الطلب في النظام ثم *ردّ على هذه الرسالة* بكلمة «تم»."
    if kind == "update":
        return "↩️ عدّل الطلب في النظام، وبعد اعتماد المالية *ردّ على هذه الرسالة* بكلمة «معتمد»."
    return ("↩️ بعد إدخال الطلب *ردّ على هذه الرسالة* برقم الطلب، وبعد اعتماد المالية ردّ بكلمة «معتمد»"
            " (أو الاثنين معاً: 12345 معتمد).")


# ======================= 1) إرسال الطلب لإدخال البيانات =======================

def submit_change(cur, customer: dict, change: dict, entry_target: str, logistics_target: str) -> list:
    """يُستدعى بدل الإرسال المباشر للوجستيك عند تفعيل مسار إدخال البيانات"""
    cur.execute("SELECT entry_snapshot, dispatched_snapshot, erp_order_no FROM order_batches WHERE id = %s FOR UPDATE;",
                (change["batch_id"],))
    batch = cur.fetchone()
    entry = batch["entry_snapshot"] or []
    after = change["after"]
    if _key(after) == _key(entry):
        return []  # لا جديد بالنسبة لإدخال البيانات (مثلاً إعادة إرسال نفس الجدول)
    kind = "new" if not entry else ("cancel" if not after else "update")

    cur.execute("""
        INSERT INTO order_submissions (batch_id, customer_id, kind, lines, prev_lines, erp_order_no)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) RETURNING id;
    """, (change["batch_id"], customer["id"], kind, json.dumps(after), json.dumps(entry), batch["erp_order_no"]))
    sid = cur.fetchone()["id"]

    extra = ["📝 *للإدخال في النظام* — " + KIND_AR[kind]]
    if batch["erp_order_no"]:
        extra.append(f"🧾 *Order No:* {batch['erp_order_no']}")
    text = oe.format_logistics_message(customer, dict(change, snapshot=entry), ref=f"FD-{sid}",
                                       extra_lines=extra, footer=_instructions(kind))
    cur.execute("UPDATE order_submissions SET entry_message = %s WHERE id = %s;", (text, sid))
    cur.execute("UPDATE order_batches SET entry_snapshot = %s::jsonb, updated_at = NOW() WHERE id = %s;",
                (json.dumps(after), change["batch_id"]))
    actions = [oe._action(entry_target, text)]

    # إلغاء طلب وصل اللوجستيك: إيقاف التجهيز فوراً دون انتظار إدخال البيانات
    dispatched = batch["dispatched_snapshot"] or []
    if kind == "cancel" and dispatched and logistics_target:
        hold = oe.format_logistics_message(
            customer, dict(change, snapshot=dispatched), ref=f"FD-{sid}",
            extra_lines=[f"🧾 *Order No:* {batch['erp_order_no']}"] if batch["erp_order_no"] else None)
        actions.append(oe._action(logistics_target, hold))
        cur.execute("UPDATE order_batches SET dispatched_snapshot = '[]'::jsonb WHERE id = %s;", (change["batch_id"],))
    return actions


# ======================= 2) ردود موظف إدخال البيانات =======================

def _parse(text: str) -> dict:
    t = REF_RE.sub(" ", (text or "").translate(_DIGITS))
    erp = ERP_RE.search(t)
    rejected = bool(REJECT_RE.search(t))
    return {"erp": erp.group(1) if erp else "", "rejected": rejected,
            "approved": (not rejected) and bool(APPROVE_RE.search(t)), "done": bool(DONE_RE.search(t)), "text": t.strip()}


def _open_submissions(cur) -> list:
    cur.execute("""
        SELECT s.*, c.company_name FROM order_submissions s JOIN customer_accounts c ON c.id = s.customer_id
        WHERE s.status IN ('awaiting', 'entered') ORDER BY s.id;
    """)
    return [dict(r) for r in cur.fetchall()]


def _get_submission(cur, sid: int) -> Optional[dict]:
    cur.execute("""
        SELECT s.*, c.company_name FROM order_submissions s JOIN customer_accounts c ON c.id = s.customer_id
        WHERE s.id = %s;
    """, (sid,))
    row = cur.fetchone()
    return dict(row) if row else None


def handle_data_entry_message(conn, msg, logistics_target: str) -> dict:
    """رسالة في محادثة إدخال البيانات: ربطها بالطلب (من الرسالة المقتبسة) ثم تسجيل الرقم أو الاعتماد"""
    chat_id = msg.chat_id.strip()
    p = _parse(msg.message_text)
    has_signal = bool(p["erp"] or p["approved"] or p["rejected"] or p["done"])
    by = f"data-entry:{msg.sender_name or msg.sender_phone}"[:150]
    actions = []
    with conn.cursor() as cur:
        ref = REF_RE.search((msg.quoted_text or "").translate(_DIGITS)) or REF_RE.search((msg.message_text or "").translate(_DIGITS))
        sub = _get_submission(cur, int(ref.group(1))) if ref else None
        if not sub:
            if not has_signal:
                return {"status": "DATA_ENTRY_IGNORED", "actions": []}
            open_subs = _open_submissions(cur)
            if len(open_subs) == 1 and not msg.quoted_text:
                sub = open_subs[0]
            elif open_subs:
                lst = "\n".join(f"• FD-{s['id']} | {s['company_name']} — {KIND_AR[s['kind']]}" for s in open_subs[:10])
                actions.append(oe._action(chat_id, "⚠️ لم أعرف أي طلب تقصد. يرجى *الرد على رسالة الطلب نفسها*.\n"
                                                    f"الطلبات المفتوحة:\n{lst}"))
                return {"status": "DATA_ENTRY_AMBIGUOUS", "actions": actions}
            else:
                return {"status": "DATA_ENTRY_IGNORED", "actions": []}
        actions = process_reply(cur, sub, p, by, logistics_target, chat_id)
    conn.commit()
    return {"status": "DATA_ENTRY_PROCESSED", "submission_id": sub["id"], "actions": actions}


def process_reply(cur, sub: dict, p: dict, by: str, logistics_target: str, ack_to: str) -> list:
    tag = f"FD-{sub['id']} | {sub['company_name']}"
    ack = lambda text: [oe._action(ack_to, text)] if ack_to else []

    if sub["status"] == "approved":
        return ack(f"ℹ️ {tag}: هذا الطلب معتمد ومرسل للوجستيك مسبقاً (رقم {sub['erp_order_no'] or '—'}).")
    if sub["status"] in ("rejected", "superseded"):
        return ack(f"ℹ️ {tag}: حالة هذا الطلب «{STATUS_AR[sub['status']]}»، لا حاجة لإجراء.")

    if sub["kind"] == "cancel" and (p["approved"] or p["done"] or p["rejected"] or p["erp"]):
        return approve_submission(cur, sub["id"], p["erp"], by, logistics_target, ack_to)

    if p["erp"]:
        _set_erp(cur, sub, p["erp"])
    if p["rejected"]:
        return reject_submission(cur, sub["id"], by, p["text"], ack_to)
    if p["approved"]:
        number = p["erp"] or sub["erp_order_no"] or _batch_erp(cur, sub["batch_id"])
        if not number:
            return ack(f"⚠️ {tag}: يرجى إرسال *رقم الطلب* في النظام مع الاعتماد (مثال: 12345 معتمد) بالرد على رسالة الطلب.")
        return approve_submission(cur, sub["id"], number, by, logistics_target, ack_to)
    if p["erp"]:
        cur.execute("""UPDATE order_submissions SET status = 'entered', entered_at = COALESCE(entered_at, NOW())
                       WHERE id = %s AND status IN ('awaiting', 'entered');""", (sub["id"],))
        return ack(f"📝 {tag}: سُجّل رقم الطلب *{p['erp']}* — بانتظار اعتماد المالية.\n"
                   "عند الاعتماد ردّ على رسالة الطلب بكلمة «معتمد».")
    return ack(f"ℹ️ {tag}: لم أفهم الرد. أرسل رقم الطلب، أو «معتمد» بعد اعتماد المالية، أو «مرفوض».")


def _batch_erp(cur, batch_id: int) -> str:
    cur.execute("SELECT erp_order_no FROM order_batches WHERE id = %s;", (batch_id,))
    row = cur.fetchone()
    return (row["erp_order_no"] or "") if row else ""


def _set_erp(cur, sub: dict, number: str):
    number = number.strip()[:60]
    cur.execute("UPDATE order_submissions SET erp_order_no = %s WHERE id = %s;", (number, sub["id"]))
    cur.execute("UPDATE order_batches SET erp_order_no = %s WHERE id = %s;", (number, sub["batch_id"]))
    sub["erp_order_no"] = number


def approve_submission(cur, sid: int, number: str, by: str, logistics_target: str, ack_to: str = "") -> list:
    """اعتماد المالية: إرسال الطلب للوجستيك برقم الطلب والحالة. يُستخدم من الواتساب ومن لوحة التحكم."""
    sub = _get_submission(cur, sid)
    if not sub:
        return []
    tag = f"FD-{sid} | {sub['company_name']}"
    ack = lambda text: [oe._action(ack_to, text)] if ack_to else []
    if sub["status"] not in OPEN_STATUSES:
        return ack(f"ℹ️ {tag}: حالة هذا الطلب «{STATUS_AR[sub['status']]}».")
    number = (number or sub["erp_order_no"] or _batch_erp(cur, sub["batch_id"]) or "").strip()[:60]

    # اعتماد متأخر لنسخة أقدم بعد اعتماد تحديث أحدث لنفس الطلب: لا نُرجع اللوجستيك لنسخة قديمة
    cur.execute("SELECT id FROM order_submissions WHERE batch_id = %s AND id > %s AND status = 'approved' LIMIT 1;",
                (sub["batch_id"], sid))
    newer = cur.fetchone()
    if newer:
        cur.execute("UPDATE order_submissions SET status = 'superseded', resolved_by = %s, resolved_at = NOW() WHERE id = %s;",
                    (by, sid))
        return ack(f"ℹ️ {tag}: تم اعتماد تحديث أحدث لهذا الطلب (FD-{newer['id']}) وأُرسل للوجستيك، فلا حاجة لإرسال هذه النسخة.")

    cur.execute("""
        UPDATE order_submissions SET status = 'approved', erp_order_no = NULLIF(%s, ''), resolved_by = %s, resolved_at = NOW(),
               entered_at = COALESCE(entered_at, NOW())
        WHERE id = %s AND status IN ('awaiting', 'entered') RETURNING id;
    """, (number, by, sid))
    if not cur.fetchone():
        return []  # اعتُمد للتو من طرف آخر
    # النسخ الأقدم المفتوحة لنفس الطلب أصبحت ضمن هذه النسخة (كل نسخة تحمل الطلب كاملاً)
    cur.execute("""UPDATE order_submissions SET status = 'superseded', resolved_by = %s, resolved_at = NOW()
                   WHERE batch_id = %s AND id < %s AND status IN ('awaiting', 'entered');""", (by, sub["batch_id"], sid))

    cur.execute("SELECT * FROM order_batches WHERE id = %s FOR UPDATE;", (sub["batch_id"],))
    batch = cur.fetchone()
    if number:
        cur.execute("UPDATE order_batches SET erp_order_no = %s WHERE id = %s;", (number, sub["batch_id"]))
    lines, dispatched = sub["lines"] or [], batch["dispatched_snapshot"] or []
    customer = _customer(cur, sub["customer_id"])

    actions, sent = [], False
    if _key(lines) != _key(dispatched):
        if logistics_target:
            change = {"after": lines, "snapshot": dispatched, "delivery_date": batch["delivery_date"],
                      "batch_id": batch["id"], "totals": oe.lines_totals(cur, lines)}
            status = "✅ *CANCELLATION CONFIRMED*" if not lines else "✅ *APPROVED*"
            text = oe.format_logistics_message(customer, change, ref=f"FD-{sid}",
                                               extra_lines=[f"🧾 *Order No:* {number or '—'}  |  {status}"])
            actions.append(oe._action(logistics_target, text))
            sent = True
        cur.execute("UPDATE order_batches SET dispatched_snapshot = %s::jsonb, updated_at = NOW() WHERE id = %s;",
                    (json.dumps(lines), batch["id"]))
    if sent:
        cur.execute("UPDATE order_submissions SET logistics_sent_at = NOW() WHERE id = %s;", (sid,))

    if sub["kind"] == "cancel":
        actions += ack(f"✅ {tag}: تم تأكيد الإلغاء" + (" وإبلاغ اللوجستيك." if sent or dispatched == [] else "."))
    elif sent:
        actions += ack(f"✅ {tag}: الطلب رقم *{number}* معتمد وأُرسل للوجستيك.")
    elif not logistics_target:
        actions += ack(f"⚠️ {tag}: اعتُمد الطلب رقم *{number}* لكن وجهة اللوجستيك غير محددة في الإعدادات.")
    else:
        actions += ack(f"✅ {tag}: الطلب رقم *{number}* معتمد (لا تغيير جديد على ما لدى اللوجستيك).")
    return actions


def reject_submission(cur, sid: int, by: str, reason: str = "", ack_to: str = "") -> list:
    """المالية لم تعتمد الطلب: لا يُرسل للوجستيك، ويُنبَّه المندوب/مسؤول النظام"""
    sub = _get_submission(cur, sid)
    if not sub or sub["status"] not in OPEN_STATUSES:
        return []
    cur.execute("""UPDATE order_submissions SET status = 'rejected', resolved_by = %s, resolved_at = NOW(), note = %s
                   WHERE id = %s AND status IN ('awaiting', 'entered') RETURNING id;""", (by, (reason or "")[:500], sid))
    if not cur.fetchone():
        return []
    tag = f"FD-{sid} | {sub['company_name']}"
    actions = [oe._action(ack_to, f"❌ {tag}: سُجّل أن الطلب غير معتمد ولن يُرسل للوجستيك.")] if ack_to else []
    customer = _customer(cur, sub["customer_id"])
    alert = oe.alert_target(cur, customer)
    if alert:
        actions.append(oe._action(alert, f"❌ *طلب لم تعتمده المالية* — {sub['company_name']}\n"
                                         f"المرجع: FD-{sid}" + (f" | رقم الطلب: {sub['erp_order_no']}" if sub["erp_order_no"] else "") +
                                         (f"\nالملاحظة: {reason}" if reason else "") +
                                         "\nيرجى التواصل مع العميل أو المالية."))
    return actions


def list_submissions(cur, scope: str = "open", limit: int = 100) -> list:
    where = "s.status IN ('awaiting', 'entered')" if scope == "open" else "TRUE"
    cur.execute(f"""
        SELECT s.id, s.batch_id, s.kind, s.status, s.erp_order_no, s.lines, s.prev_lines, s.resolved_by, s.note,
               s.created_at, s.entered_at, s.resolved_at, s.logistics_sent_at, b.delivery_date, c.company_name, c.brand_name
        FROM order_submissions s JOIN order_batches b ON b.id = s.batch_id JOIN customer_accounts c ON c.id = s.customer_id
        WHERE {where} ORDER BY s.id DESC LIMIT %s;
    """, (max(1, min(limit, 300)),))
    return [dict(r) for r in cur.fetchall()]

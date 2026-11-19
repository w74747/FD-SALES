"""
direct_chat.py - الرد الآلي على المحادثات المباشرة (أي رقم يراسل رقم الشركة خارج مجموعات العملاء)

قواعد العمل:
- الترحيب (والكتالوج) يُرسل مرة واحدة فقط لكل شخص خلال 30 يوماً، وليس مع كل رسالة.
- البوت يجيب من قاعدة المعرفة فقط، ولا يذكر أي سعر، ولا يستلم أو يعدّل طلبات.
- ما لا يعرفه البوت: رد مؤقت للشخص (مرة كل ساعتين كحد أقصى) + تنبيه لمسؤول النظام برقم الشخص وسؤاله.
- إذا رد أحد من الفريق يدوياً في المحادثة، يسكت البوت 30 دقيقة حتى لا يقاطعه.
- حد أقصى لعدد ردود البوت في الساعة لكل محادثة، لحماية التكلفة ومنع الإزعاج.

منطق القرار (الدوال البحتة) منفصل عن قاعدة البيانات حتى يمكن اختباره.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("SalesCRM.direct")

SETTING_KEY = "direct_auto_reply_enabled"
STAFF_QUIET_MINUTES = 30
MAX_BOT_REPLIES_PER_HOUR = 8
WELCOME_EVERY_DAYS = 30
HOLD_EVERY_HOURS = 2
CONTEXT_MESSAGES = 8

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS direct_chat_messages (
        id SERIAL PRIMARY KEY,
        chat_id VARCHAR(100) NOT NULL,
        role VARCHAR(10) NOT NULL,          -- contact / bot / staff
        kind VARCHAR(20) DEFAULT 'text',    -- text / welcome / answer / hold
        body TEXT DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );""",
    "CREATE INDEX IF NOT EXISTS idx_direct_chat_messages_chat ON direct_chat_messages (chat_id, created_at DESC);",
]

HOLD_TEXT = {
    "ar": "شكراً لتواصلكم مع شركة تنمية الغذاء 🌷 سيقوم أحد أعضاء فريقنا بالرد عليكم قريباً.",
    "en": "Thank you for contacting Food Development Company 🌷 A member of our team will get back to you shortly.",
}


def init_schema(run_ddl):
    for stmt in SCHEMA:
        run_ddl(stmt)


def is_direct_chat(chat_id: str) -> bool:
    """محادثة خاصة مع شخص (وليست مجموعة أو قناة أو حالة)"""
    chat_id = (chat_id or "").strip()
    return bool(chat_id) and not chat_id.endswith("@g.us") and "@newsletter" not in chat_id \
        and "status@broadcast" not in chat_id and "@broadcast" not in chat_id


def detect_language(text: str) -> str:
    """العربية إذا غلبت الحروف العربية، وإلا الإنجليزية"""
    arabic = len(re.findall(r"[؀-ۿ]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return "ar" if arabic >= latin and arabic > 0 else ("en" if latin else "ar")


# ----------------------------- القرار (بدون قاعدة بيانات) -----------------------------

def summarize_history(rows: list, now: datetime) -> dict:
    """
    rows: رسائل المحادثة السابقة (الأحدث أولاً) بالحقول role / kind / created_at.
    يعيد ما نحتاجه للقرار: هل الفريق نشط؟ كم رد للبوت في الساعة؟ هل رحّبنا؟ متى آخر رد مؤقت؟
    """
    def age(r):
        ts = r["created_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return now - ts

    return {
        "staff_active": any(r["role"] == "staff" and age(r) < timedelta(minutes=STAFF_QUIET_MINUTES) for r in rows),
        "bot_replies_last_hour": sum(1 for r in rows if r["role"] == "bot" and age(r) < timedelta(hours=1)),
        "welcomed_recently": any(r["role"] == "bot" and age(r) < timedelta(days=WELCOME_EVERY_DAYS) for r in rows),
        "hold_recently": any(r["role"] == "bot" and r.get("kind") == "hold" and age(r) < timedelta(hours=HOLD_EVERY_HOURS)
                             for r in rows),
        "talked_before": any(r["role"] in ("bot", "staff") for r in rows),
    }


def plan_reply(state: dict, answer: Optional[dict], welcome_enabled: bool, welcome_text: str, lang: str) -> dict:
    """
    تحديد ما يُرسل للشخص وهل يُنبَّه مسؤول النظام.
    answer: ناتج Claude (needs_reply / can_answer / answer / topic / escalation_reason) أو None إذا تعذر الاتصال.
    يعيد {reply, kind, send_catalog, alert, alert_reason}.
    """
    out = {"reply": "", "kind": "", "send_catalog": False, "alert": False, "alert_reason": ""}
    if state["staff_active"]:
        return out  # أحد من الفريق يتحدث مع الشخص الآن
    if state["bot_replies_last_hour"] >= MAX_BOT_REPLIES_PER_HOUR:
        return out

    parts = []
    first_contact = welcome_enabled and (welcome_text or "").strip() and not state["welcomed_recently"]
    if first_contact:
        parts.append(welcome_text.strip())
        out["send_catalog"] = True
        out["kind"] = "welcome"

    if answer is None:
        # تعذر الوصول إلى Claude: لا نخترع رداً، ننبّه الفريق فقط
        out["alert"], out["alert_reason"] = True, "تعذر الرد الآلي (خدمة الذكاء الاصطناعي غير متاحة)"
    elif answer.get("needs_reply") is False and state["talked_before"]:
        pass  # شكراً / تمام / رموز تعبيرية: لا حاجة للرد
    elif answer.get("can_answer") and (answer.get("answer") or "").strip():
        parts.append(answer["answer"].strip())
        out["kind"] = "answer"
    else:
        out["alert"] = True
        out["alert_reason"] = (answer.get("escalation_reason") or "").strip()
        if not state["hold_recently"] and not first_contact:
            parts.append(HOLD_TEXT.get(lang, HOLD_TEXT["en"]))
            out["kind"] = "hold"
        elif first_contact:
            out["kind"] = "hold"  # الترحيب يكفي كرد مؤقت ويُحسب كذلك

    out["reply"] = "\n\n".join(parts)
    return out


def alert_text(lang: str, name: str, phone: str, message: str, topic: str, reason: str) -> str:
    who = f"{name} (+{phone})" if phone and name and name != phone else f"+{phone}" if phone else name
    if lang == "ar":
        text = f"📩 *رسالة مباشرة تحتاج ردكم*\nمن: {who}\nالموضوع: {topic or '-'}\nالرسالة: {message[:400]}"
        return text + (f"\nسبب التحويل: {reason}" if reason else "")
    text = f"📩 *Direct message needs your reply*\nFrom: {who}\nTopic: {topic or '-'}\nMessage: {message[:400]}"
    return text + (f"\nEscalation reason: {reason}" if reason else "")


def format_conversation(rows_oldest_first: list) -> str:
    label = {"contact": "Person", "bot": "Us (bot)", "staff": "Us (team)"}
    return "\n".join(f"{label.get(r['role'], r['role'])}: {(r.get('body') or '')[:500]}" for r in rows_oldest_first)


# ----------------------------- قاعدة البيانات -----------------------------

def load_history(cur, chat_id: str, limit: int = 40) -> list:
    cur.execute("""SELECT role, kind, body, created_at FROM direct_chat_messages
                   WHERE chat_id = %s AND created_at > NOW() - INTERVAL '31 days'
                   ORDER BY created_at DESC LIMIT %s;""", (chat_id, limit))
    return [dict(r) for r in cur.fetchall()]


def record(cur, chat_id: str, role: str, body: str, kind: str = "text"):
    cur.execute("INSERT INTO direct_chat_messages (chat_id, role, kind, body) VALUES (%s, %s, %s, %s);",
                (chat_id, role, kind, (body or "")[:4000]))


def is_bot_echo(cur, chat_id: str, text: str) -> bool:
    """رد البوت قد يصل مرة أخرى كرسالة من رقم الشركة؛ لا نعدّه رداً يدوياً من الفريق"""
    cur.execute("""SELECT 1 FROM direct_chat_messages
                   WHERE chat_id = %s AND role = 'bot' AND body = %s
                     AND created_at > NOW() - INTERVAL '10 minutes' LIMIT 1;""", (chat_id, (text or "")[:4000]))
    return cur.fetchone() is not None


def welcome_once(cur, chat_id: str, text: str, welcome_text: str) -> bool:
    """
    الترحيب بدون رد آلي (الوضع القديم): يُرسل مرة واحدة لكل شخص خلال 30 يوماً بدل كل رسالة.
    يعيد True إذا يجب إرسال الترحيب الآن (ويسجّله).
    """
    if not (welcome_text or "").strip():
        return False
    history = load_history(cur, chat_id)
    record(cur, chat_id, "contact", text)
    if summarize_history(history, datetime.now(timezone.utc))["welcomed_recently"]:
        return False
    record(cur, chat_id, "bot", welcome_text, "welcome")
    return True


async def handle(conn, msg, conf: dict, clean_phone: str, catalog_available: bool,
                 kb_builder, answer_fn, admin_target: str, admin_lang: str) -> dict:
    """
    معالجة رسالة مباشرة من شخص غير مسجل كعميل.
    kb_builder(cur) -> نص قاعدة المعرفة؛ answer_fn(kb, message, conversation, lang) -> رد Claude.
    """
    chat_id = msg.chat_id.strip()
    text = (msg.message_text or "").strip()
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        if msg.is_from_me:
            # رسالة من رقم الشركة: إن كانت صدى لرد البوت نفسه نتجاهلها، وإلا فهي رد يدوي من الفريق فيسكت البوت
            if is_bot_echo(cur, chat_id, text):
                conn.commit()
                return {"status": "DIRECT_BOT_ECHO"}
            record(cur, chat_id, "staff", text)
            conn.commit()
            return {"status": "DIRECT_STAFF_RECORDED"}
        history = load_history(cur, chat_id)
        record(cur, chat_id, "contact", text)
        kb_text = kb_builder(cur) if text else ""
    conn.commit()  # لا نُبقي معاملة مفتوحة أثناء انتظار النموذج

    state = summarize_history(history, now)
    lang = detect_language(text)
    welcome_enabled = conf.get("exhibition_auto_reply_enabled") == "true"
    welcome_text = conf.get("exhibition_greeting_text", "")

    answer = None
    if text and not state["staff_active"] and state["bot_replies_last_hour"] < MAX_BOT_REPLIES_PER_HOUR:
        conversation = format_conversation(list(reversed(history[:CONTEXT_MESSAGES])))
        answer = await answer_fn(kb_text, text, conversation, lang)

    plan = plan_reply(state, answer, welcome_enabled, welcome_text, lang)
    plan["send_catalog"] = plan["send_catalog"] and catalog_available

    actions = []
    with conn.cursor() as cur:
        if plan["reply"]:
            record(cur, chat_id, "bot", plan["reply"], plan["kind"] or "answer")
        if plan["alert"] and admin_target:
            actions.append({"to": admin_target, "text": alert_text(
                admin_lang, msg.sender_name or "", clean_phone, text,
                (answer or {}).get("topic", ""), plan["alert_reason"])})
    conn.commit()

    logger.info(f"[Direct] {chat_id}: kind={plan['kind'] or '-'} alert={plan['alert']} "
                f"staff_active={state['staff_active']} replies_1h={state['bot_replies_last_hour']}")
    return {"status": "DIRECT_PROCESSED", "reply_text": plan["reply"] or None,
            "send_catalog": plan["send_catalog"], "actions": actions}

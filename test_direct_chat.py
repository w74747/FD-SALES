"""اختبارات الرد الآلي على المحادثات المباشرة (direct_chat) — قاعدة بيانات وهمية"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import direct_chat as dc

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def row(role, minutes_ago, kind="text", body=""):
    return {"role": role, "kind": kind, "body": body, "created_at": NOW - timedelta(minutes=minutes_ago)}


def state(rows):
    return dc.summarize_history(rows, NOW)


ANSWER = {"needs_reply": True, "can_answer": True, "answer": "التوصيل يومياً من السبت إلى الخميس."}
UNKNOWN = {"needs_reply": True, "can_answer": False, "topic": "سعر", "escalation_reason": "لا توجد أسعار"}


# ----------------------------- أدوات -----------------------------

def test_is_direct_chat():
    assert dc.is_direct_chat("96890000000@s.whatsapp.net")
    assert dc.is_direct_chat("123456789@lid")
    assert not dc.is_direct_chat("120363000000000000@g.us")
    assert not dc.is_direct_chat("status@broadcast")
    assert not dc.is_direct_chat("123@newsletter")
    assert not dc.is_direct_chat("")


def test_detect_language():
    assert dc.detect_language("كم مدة التوصيل؟") == "ar"
    assert dc.detect_language("What are your delivery days?") == "en"
    assert dc.detect_language("hi ممكن الكتالوج") == "ar"
    assert dc.detect_language("👍") == "ar"


# ----------------------------- القرار -----------------------------

def test_first_contact_gets_welcome_and_answer():
    plan = dc.plan_reply(state([]), ANSWER, True, "أهلاً بكم في شركة تنمية الغذاء", "ar")
    assert plan["reply"].startswith("أهلاً بكم")
    assert ANSWER["answer"] in plan["reply"]
    assert plan["send_catalog"] is True
    assert plan["alert"] is False


def test_welcome_only_once():
    plan = dc.plan_reply(state([row("bot", 60 * 24, "welcome")]), ANSWER, True, "أهلاً", "ar")
    assert not plan["reply"].startswith("أهلاً")
    assert plan["reply"] == ANSWER["answer"]
    assert plan["send_catalog"] is False


def test_welcome_again_after_30_days():
    plan = dc.plan_reply(state([row("bot", 60 * 24 * 31, "welcome")]), ANSWER, True, "أهلاً", "ar")
    assert plan["reply"].startswith("أهلاً")


def test_unknown_question_hold_and_alert():
    plan = dc.plan_reply(state([row("bot", 600, "answer")]), UNKNOWN, False, "", "ar")
    assert plan["reply"] == dc.HOLD_TEXT["ar"]
    assert plan["kind"] == "hold"
    assert plan["alert"] is True and plan["alert_reason"] == "لا توجد أسعار"


def test_hold_not_repeated_within_two_hours_but_still_alerts():
    plan = dc.plan_reply(state([row("bot", 30, "hold")]), UNKNOWN, False, "", "ar")
    assert plan["reply"] == ""
    assert plan["alert"] is True


def test_first_contact_unknown_question_welcome_is_the_hold():
    plan = dc.plan_reply(state([]), UNKNOWN, True, "Welcome!", "en")
    assert plan["reply"] == "Welcome!"
    assert plan["alert"] is True


def test_staff_active_bot_silent():
    plan = dc.plan_reply(state([row("staff", 10)]), ANSWER, True, "أهلاً", "ar")
    assert plan == {"reply": "", "kind": "", "send_catalog": False, "alert": False, "alert_reason": ""}


def test_staff_quiet_period_ends():
    plan = dc.plan_reply(state([row("staff", 31)]), ANSWER, False, "", "ar")
    assert plan["reply"] == ANSWER["answer"]


def test_rate_limit_per_hour():
    rows = [row("bot", i * 5, "answer") for i in range(dc.MAX_BOT_REPLIES_PER_HOUR)]
    plan = dc.plan_reply(state(rows), ANSWER, False, "", "ar")
    assert plan["reply"] == "" and plan["alert"] is False


def test_thanks_after_conversation_needs_no_reply():
    plan = dc.plan_reply(state([row("bot", 5, "answer")]), {"needs_reply": False, "can_answer": False}, False, "", "ar")
    assert plan["reply"] == "" and plan["alert"] is False


def test_ai_unavailable_alerts_and_never_invents():
    plan = dc.plan_reply(state([row("bot", 600)]), None, False, "", "ar")
    assert plan["reply"] == ""
    assert plan["alert"] is True


def test_alert_text():
    t = dc.alert_text("ar", "Ali", "96890000000", "كم السعر؟", "سعر", "لا توجد أسعار")
    assert "+96890000000" in t and "كم السعر؟" in t and "سبب التحويل" in t
    assert "Direct message" in dc.alert_text("en", "", "968", "hi", "", "")


# ----------------------------- handle مع قاعدة بيانات وهمية -----------------------------

class FakeDB:
    """يحاكي جدول direct_chat_messages فقط"""

    def __init__(self, rows=None):
        self.rows = rows or []  # dicts: chat_id, role, kind, body, created_at

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass


class FakeCursor:
    def __init__(self, db):
        self.db, self.result = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        sql = re.sub(r"\s+", " ", sql)
        if sql.startswith("INSERT INTO direct_chat_messages"):
            chat_id, role, kind, body = params
            self.db.rows.append({"chat_id": chat_id, "role": role, "kind": kind, "body": body,
                                 "created_at": datetime.now(timezone.utc)})
        elif "role = 'bot' AND body = %s" in sql:
            chat_id, body = params
            self.result = [1] if any(r["chat_id"] == chat_id and r["role"] == "bot" and r["body"] == body
                                     for r in self.db.rows) else []
        elif sql.startswith("SELECT role, kind, body, created_at FROM direct_chat_messages"):
            chat_id, limit = params
            rows = sorted((r for r in self.db.rows if r["chat_id"] == chat_id), key=lambda r: r["created_at"], reverse=True)
            self.result = rows[:limit]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


def msg(text, from_me=False, chat="96890000000@s.whatsapp.net"):
    return SimpleNamespace(chat_id=chat, message_text=text, is_from_me=from_me, sender_name="Ali")


CONF = {"exhibition_auto_reply_enabled": "true", "exhibition_greeting_text": "أهلاً بكم"}


def run(db, m, answer=ANSWER, conf=CONF):
    calls = []

    async def answer_fn(kb, text, conversation, lang):
        calls.append({"kb": kb, "text": text, "conversation": conversation, "lang": lang})
        return answer

    out = asyncio.run(dc.handle(db, m, conf, "96890000000", True, lambda cur: "KB TEXT", answer_fn,
                                "96899999999", "ar"))
    return out, calls


def test_handle_full_flow_and_staff_takeover():
    db = FakeDB()
    out, calls = run(db, msg("متى التوصيل؟"))
    assert out["reply_text"].startswith("أهلاً بكم") and out["send_catalog"] is True
    assert calls[0]["kb"] == "KB TEXT" and calls[0]["lang"] == "ar"
    assert [r["role"] for r in db.rows] == ["contact", "bot"]

    # صدى رد البوت من رقم الشركة لا يُعدّ رداً من الفريق
    out, _ = run(db, msg(out["reply_text"], from_me=True))
    assert out["status"] == "DIRECT_BOT_ECHO"

    # رد يدوي من الفريق ← البوت يسكت
    out, _ = run(db, msg("أهلاً أخي، معك سالم من المبيعات", from_me=True))
    assert out["status"] == "DIRECT_STAFF_RECORDED"
    out, calls = run(db, msg("تمام، وكم السعر؟"))
    assert out["reply_text"] is None and calls == []
    assert out["actions"] == []


def test_handle_unknown_alerts_admin():
    db = FakeDB()
    out, _ = run(db, msg("كم سعر الكرتون؟"), answer=UNKNOWN, conf={})
    assert out["reply_text"] == dc.HOLD_TEXT["ar"]
    assert out["actions"][0]["to"] == "96899999999"
    assert "كم سعر الكرتون؟" in out["actions"][0]["text"]


def test_welcome_once_legacy_mode():
    db = FakeDB()
    cur = db.cursor()
    assert dc.welcome_once(cur, "c1", "hi", "أهلاً") is True
    assert dc.welcome_once(cur, "c1", "hi again", "أهلاً") is False
    assert dc.welcome_once(cur, "c2", "hi", "أهلاً") is True
    assert dc.welcome_once(cur, "c3", "hi", "") is False

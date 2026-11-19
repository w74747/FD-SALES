"""
knowledge.py - قاعدة معرفة البوت للرد على استفسارات العملاء

- مسؤول النظام يرفع ملفات (قائمة منتجات، أسعار، عن الشركة...) أو يكتب ملاحظات نصية.
- يُستخرج النص مرة واحدة عند الرفع ويُحفظ؛ الملفات الممسوحة ضوئياً والصور تُقرأ بـ Claude.
- عند الاستفسار: يجيب البوت من هذه المعلومات فقط، وما ليس فيها يُحوَّل للفريق.
"""

import base64
import logging
import os
from typing import Optional

import ai_client
import media_tools

logger = logging.getLogger("SalesCRM.knowledge")

# الحدود بالأحرف. Claude Haiku 5.5 يقبل حتى مليون token، لكن السعر يتضاعف 5 مرات فوق 100 ألف token للطلب،
# لذلك الحد الافتراضي ~200 ألف حرف (أقل من 100 ألف token تقريباً). يمكن تغييره من KB_MAX_CHARS.
MAX_DOC_CHARS = int(os.getenv("KB_MAX_DOC_CHARS", "150000"))
MAX_CONTEXT_CHARS = int(os.getenv("KB_MAX_CHARS", "200000"))

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS knowledge_documents (
        id SERIAL PRIMARY KEY,
        title VARCHAR(200) NOT NULL,
        file_name VARCHAR(255) DEFAULT '',
        mime_type VARCHAR(120) DEFAULT 'text/plain',
        content_text TEXT NOT NULL,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );""",
]

TRANSCRIBE_TOOL = {
    "name": "record_document_text",
    "description": "Record the full textual content of the document/image.",
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "All text content. Tables as rows with | between cells. Keep prices, units and product names exact."}},
        "required": ["text"],
    },
}


def init_schema(run_ddl):
    for stmt in SCHEMA:
        run_ddl(stmt)


async def extract_document_text(raw: bytes, mimetype: str, filename: str) -> str:
    """نص الملف المرفوع: محلياً إن أمكن، وإلا بقراءة Claude (PDF ممسوح أو صورة)"""
    text = media_tools.extract_text_locally(raw, mimetype, filename)
    kind = media_tools.kind_of(mimetype, filename)
    if len(text.strip()) < 80 and kind in ("pdf", "image") and ai_client.is_enabled():
        block = ai_client.media_block(base64.b64encode(raw).decode("ascii"),
                                      "application/pdf" if kind == "pdf" else mimetype)
        if block:
            result = await ai_client.call_tool(
                "You transcribe business documents (product lists, price lists, company profiles) into plain text faithfully.",
                [block, {"type": "text", "text": "Transcribe this document completely."}],
                TRANSCRIBE_TOOL, use_vision=True, max_tokens=8000)
            if result and result.get("text"):
                text = result["text"]
    return text.strip()[:MAX_DOC_CHARS]


def _catalog_text(cur) -> str:
    try:
        cur.execute("""SELECT * FROM products_catalog ORDER BY category NULLS LAST, sku LIMIT 1000;""")
        rows = cur.fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    g = lambda r, k: r.get(k) if r.get(k) not in (None, "") else None
    fmt = lambda v: f"{float(v):g}" if isinstance(v, (int, float)) or hasattr(v, "as_tuple") else str(v)
    return ("(code | name | category | weight/piece g | pcs/pack | packs/box | pcs/box | "
            "shelf life frozen days | shelf life after thawing +4C | preparation)\n" + "\n".join(
        " | ".join(fmt(x) if x is not None else "-" for x in (
            g(r, "sku"), g(r, "name_en") or g(r, "name_ar"), g(r, "category"), g(r, "piece_weight_g"),
            g(r, "pieces_per_pack"), g(r, "packs_per_box"), g(r, "pieces_per_box"),
            g(r, "shelf_life_frozen"), g(r, "shelf_life_secondary"), g(r, "preparation")))
        for r in rows))


def assemble(sections: list, limit: int = None) -> tuple:
    """
    جمع أقسام قاعدة المعرفة ضمن الحد المسموح، مع تقرير صريح عمّا دخل وما قُصّ أو استُبعد
    (بدلاً من القص الصامت الذي كان يُسقط آخر الملفات المضافة دون أن يعرف أحد).
    sections: [(العنوان، النص)] بالترتيب. يعيد (النص، التقرير).
    """
    limit = MAX_CONTEXT_CHARS if limit is None else limit
    parts, report, used = [], [], 0
    for title, text in sections:
        block = f"=== {title} ===\n{text}"
        sep = 2 if parts else 0
        room = limit - used - sep
        if room <= 0:
            report.append({"title": title, "chars": len(text), "included": 0, "status": "excluded"})
            continue
        status = "full"
        if len(block) > room:
            block, status = block[:room], "truncated"
        parts.append(block)
        used += len(block) + sep
        report.append({"title": title, "chars": len(text), "included": len(block), "status": status})
    return "\n\n".join(parts), {"limit": limit, "used": used, "sections": report,
                                  "complete": all(r["status"] == "full" for r in report)}


def _sections(cur) -> list:
    """الكتالوج أولاً (بيانات المنتجات الرسمية)، ثم الملفات المفعّلة بترتيب إضافتها"""
    sections = []
    catalog = _catalog_text(cur)
    if catalog:
        sections.append(("Product catalog (from the system)", catalog))
    cur.execute("SELECT title, content_text FROM knowledge_documents WHERE is_active = TRUE ORDER BY id;")
    for r in cur.fetchall():
        sections.append((r["title"], r["content_text"] or ""))
    return sections


def build_context(cur) -> str:
    """كل المعلومات المتاحة للرد: كتالوج المنتجات من النظام + الملفات المفعّلة"""
    text, report = assemble(_sections(cur))
    if not report["complete"]:
        logger.warning(f"Knowledge base exceeds {report['limit']} chars; some content was cut: "
                       f"{[r['title'] for r in report['sections'] if r['status'] != 'full']}")
    return text


def context_report(cur) -> dict:
    """ما يراه البوت فعلاً من قاعدة المعرفة (يُعرض في لوحة التحكم)"""
    return assemble(_sections(cur))[1]


ANSWER_TOOL = {
    "name": "answer_customer",
    "description": "Answer the customer's question using ONLY the company knowledge provided, or escalate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "can_answer": {"type": "boolean", "description": "True ONLY if the knowledge fully and explicitly answers the question."},
            "answer": {"type": "string", "description": "Short, polite WhatsApp reply in the customer's language. Empty if can_answer=false."},
            "escalation_reason": {"type": "string", "description": "If can_answer=false: what information is missing (in Arabic, for the team)."},
        },
        "required": ["can_answer"],
    },
}

ANSWER_SYSTEM = """You reply on behalf of Food Development Co. (FDC), a B2B bakery in Oman, inside a customer's WhatsApp group.
Use ONLY the COMPANY KNOWLEDGE below. Never guess or invent prices, stock, delivery dates, discounts, ingredients or policies.
If the knowledge does not explicitly contain the answer, set can_answer=false (a team member will reply).
Prices: if an "AGREED PRICES FOR THIS CUSTOMER ONLY" section is present you may quote those exact prices (OMR, with the unit). Otherwise do not quote any price: say the sales team will confirm the price. Never quote prices that belong to another customer.
Never discuss the customer's account balance, invoices, payments or complaints compensation: escalate.
Keep replies short (1-4 lines), friendly and professional, in the same language as the customer (Arabic or English)."""


async def answer_question(kb_text: str, customer_name: str, question: str, conversation: str, language: str) -> Optional[dict]:
    if not kb_text.strip():
        return {"can_answer": False, "escalation_reason": "قاعدة المعرفة فارغة"}
    content = knowledge_blocks(kb_text) + [{"type": "text", "text":
                f"CUSTOMER: {customer_name}\n"
                f"RECENT CONVERSATION:\n{conversation}\n\nQUESTION TO ANSWER (reply in {'Arabic' if language != 'en' else 'English'}):\n{question}"}]
    return await ai_client.call_tool(ANSWER_SYSTEM, content, ANSWER_TOOL, use_vision=False, max_tokens=800)


def knowledge_blocks(kb_text: str) -> list:
    """
    قاعدة المعرفة ككتلة مستقلة مُعلَّمة بـ cache_control وتسبق السؤال:
    الأسئلة المتتالية خلال دقائق تقرأها من الذاكرة المؤقتة بعُشر السعر بدل إعادة معالجتها.
    """
    return [{"type": "text", "text": f"COMPANY KNOWLEDGE:\n{kb_text}", "cache_control": {"type": "ephemeral"}}]


# ----------------------------- المحادثات المباشرة (أي رقم) -----------------------------

DIRECT_TOOL = {
    "name": "reply_to_contact",
    "description": "Decide how to reply to a person who messaged the company WhatsApp number directly.",
    "input_schema": {
        "type": "object",
        "properties": {
            "needs_reply": {"type": "boolean",
                            "description": "False ONLY for messages that need no answer (ok, thanks, emoji, bye) after we already talked."},
            "can_answer": {"type": "boolean", "description": "True ONLY if the knowledge fully and explicitly answers the message."},
            "answer": {"type": "string", "description": "Short, polite WhatsApp reply in the person's language. Empty if can_answer=false."},
            "topic": {"type": "string", "description": "2-6 words in Arabic describing what the person wants (for the team)."},
            "escalation_reason": {"type": "string", "description": "If can_answer=false: what is missing (in Arabic, for the team)."},
        },
        "required": ["needs_reply", "can_answer"],
    },
}

DIRECT_SYSTEM = """You reply on behalf of Food Development Co. (FDC), a B2B bakery in Oman, to a person who messaged the company's WhatsApp number directly. They may be a new business lead, an existing customer's employee, or anyone else. You do not know who they are.
Use ONLY the COMPANY KNOWLEDGE provided. Never guess or invent prices, stock, delivery dates, discounts, ingredients, certifications or policies.
Never quote any price, even if a price appears in the knowledge: prices depend on the customer's agreement, so say the sales team will share pricing.
Never confirm, take, change or cancel an order here, and never discuss invoices, payments, account balances or complaint compensation: set can_answer=false so the team follows up.
If the knowledge does not explicitly contain the answer, set can_answer=false (a team member will reply).
Greetings: if the message is only a greeting, reply briefly and ask how you can help (can_answer=true).
Keep replies short (1-4 lines), friendly and professional, in the same language as the person (Arabic or English). Do not use markdown headings."""


async def answer_direct(kb_text: str, message: str, conversation: str, language: str) -> Optional[dict]:
    """رد على شخص راسل رقم الشركة مباشرة: من قاعدة المعرفة فقط، بدون أسعار أو طلبات"""
    if not kb_text.strip():
        return {"needs_reply": True, "can_answer": False, "escalation_reason": "قاعدة المعرفة فارغة"}
    content = knowledge_blocks(kb_text) + [{"type": "text", "text":
                f"RECENT CONVERSATION WITH THIS PERSON (oldest first):\n{conversation or '(none)'}\n\n"
                f"LATEST MESSAGE (reply in {'Arabic' if language != 'en' else 'English'}):\n{message}"}]
    return await ai_client.call_tool(DIRECT_SYSTEM, content, DIRECT_TOOL, use_vision=False, max_tokens=800)

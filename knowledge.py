"""
knowledge.py - قاعدة معرفة البوت للرد على استفسارات العملاء

- مسؤول النظام يرفع ملفات (قائمة منتجات، أسعار، عن الشركة...) أو يكتب ملاحظات نصية.
- يُستخرج النص مرة واحدة عند الرفع ويُحفظ؛ الملفات الممسوحة ضوئياً والصور تُقرأ بـ Claude.
- عند الاستفسار: يجيب البوت من هذه المعلومات فقط، وما ليس فيها يُحوَّل للفريق.
"""

import base64
import logging
from typing import Optional

import ai_client
import media_tools

logger = logging.getLogger("SalesCRM.knowledge")

MAX_DOC_CHARS = 60000
MAX_CONTEXT_CHARS = 90000

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


def build_context(cur) -> str:
    """كل المعلومات المتاحة للرد: الملفات المفعّلة + كتالوج المنتجات من النظام"""
    parts = []
    cur.execute("SELECT title, content_text FROM knowledge_documents WHERE is_active = TRUE ORDER BY id;")
    for r in cur.fetchall():
        parts.append(f"=== {r['title']} ===\n{r['content_text']}")
    try:
        cur.execute("""SELECT * FROM products_catalog ORDER BY category NULLS LAST, sku LIMIT 500;""")
        rows = cur.fetchall()
        if rows:
            g = lambda r, k: r.get(k) if r.get(k) not in (None, "") else None
            fmt = lambda v: f"{float(v):g}" if isinstance(v, (int, float)) or hasattr(v, "as_tuple") else str(v)
            parts.append("=== Product catalog (code | name | category | weight/piece g | pcs/pack | packs/box | pcs/box | "
                         "shelf life frozen days | shelf life after thawing +4C | preparation) ===\n" + "\n".join(
                " | ".join(fmt(x) if x is not None else "-" for x in (
                    g(r, "sku"), g(r, "name_en") or g(r, "name_ar"), g(r, "category"), g(r, "piece_weight_g"),
                    g(r, "pieces_per_pack"), g(r, "packs_per_box"), g(r, "pieces_per_box"),
                    g(r, "shelf_life_frozen"), g(r, "shelf_life_secondary"), g(r, "preparation")))
                for r in rows))
    except Exception:
        pass
    text = "\n\n".join(parts)
    return text[:MAX_CONTEXT_CHARS]


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
If prices in the knowledge are general list prices, say they are list prices and that the sales team can confirm the customer's agreed price.
Never discuss the customer's account balance, invoices, payments or complaints compensation: escalate.
Keep replies short (1-4 lines), friendly and professional, in the same language as the customer (Arabic or English)."""


async def answer_question(kb_text: str, customer_name: str, question: str, conversation: str, language: str) -> Optional[dict]:
    if not kb_text.strip():
        return {"can_answer": False, "escalation_reason": "قاعدة المعرفة فارغة"}
    content = [{"type": "text", "text":
                f"COMPANY KNOWLEDGE:\n{kb_text}\n\n---\nCUSTOMER: {customer_name}\n"
                f"RECENT CONVERSATION:\n{conversation}\n\nQUESTION TO ANSWER (reply in {'Arabic' if language != 'en' else 'English'}):\n{question}"}]
    return await ai_client.call_tool(ANSWER_SYSTEM, content, ANSWER_TOOL, use_vision=False, max_tokens=800)

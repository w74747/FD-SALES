"""
ai_client.py - الاتصال بـ Claude (Anthropic Messages API) لفهم رسائل الطلبات

- نموذج سريع للرسائل النصية، ونموذج أقوى لقراءة صور الجداول وملفات PDF.
- المخرجات منظمة دائماً عبر "أداة" إلزامية (tool_choice) بدلاً من نص حر يحتاج تحليلاً.
"""

import base64
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("SalesCRM.ai")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_TEXT_MODEL = os.getenv("ANTHROPIC_TEXT_MODEL", "claude-haiku-5-5")
ANTHROPIC_VISION_MODEL = os.getenv("ANTHROPIC_VISION_MODEL", "claude-sonnet-5-5")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def is_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


def media_block(media_b64: str, mimetype: str) -> Optional[dict]:
    """تحويل الصورة/PDF إلى كتلة محتوى يقرأها النموذج"""
    mimetype = (mimetype or "").split(";")[0].strip().lower()
    if mimetype in SUPPORTED_IMAGE_TYPES:
        return {"type": "image", "source": {"type": "base64", "media_type": mimetype, "data": media_b64}}
    if mimetype == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": media_b64}}
    return None


def spreadsheet_to_text(media_b64: str, max_rows: int = 80, max_cols: int = 20) -> str:
    """ملفات Excel المرسلة كطلبيات تُحوَّل إلى جدول نصي يقرأه النموذج"""
    try:
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(media_b64)), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets[:2]:
            out.append(f"### Sheet: {ws.title}")
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if r >= max_rows:
                    break
                cells = ["" if v is None else str(v).strip() for v in row[:max_cols]]
                if any(cells):
                    out.append(" | ".join(cells))
        return "\n".join(out)
    except Exception as e:
        logger.warning(f"Spreadsheet parse failed: {e}")
        return ""


# النماذج التي ترفض إجبار الأداة (tool_choice: tool/any) — نستخدم معها auto مع تعليمات صريحة وإعادة محاولة
_NO_FORCED_TOOL_PREFIXES = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-fable-5-2",
                            "claude-mythos-5-1", "claude-mythos-5-2")

# آخر نتيجة اتصال بـ Claude (تُعرض في لوحة التحكم بدل افتراض أن وجود المفتاح يعني أنه يعمل)
last_status = {"ok": None, "code": None, "message": "", "at": None}


def forced_tool_supported(model: str) -> bool:
    return not (model or "").startswith(_NO_FORCED_TOOL_PREFIXES)


def build_payload(system: str, content: list, tool: dict, model: str, max_tokens: int) -> dict:
    """
    بناء الطلب حسب قدرات النموذج:
    - لا نرسل temperature: نماذج Claude الحالية (Haiku 5.5 / Sonnet 5.5) ترفض أي قيمة غير الافتراضية بخطأ 400.
    - النظام (system) يُرسل ككتلة مع cache_control حتى يُخزَّن مؤقتاً مع الأدوات.
    - النماذج التي ترفض إجبار الأداة: tool_choice=auto + تعليمات صريحة، ومساحة أكبر لأن التفكير يُحسب من max_tokens.
    """
    forced = forced_tool_supported(model)
    sys_text = system if forced else (
        f"{system}\n\nYou MUST respond by calling the `{tool['name']}` tool exactly once. Do not reply with plain text.")
    return {
        "model": model,
        "max_tokens": max_tokens if forced else max(max_tokens, 8000),
        "system": [{"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]} if forced else {"type": "auto"},
        "messages": [{"role": "user", "content": content}],
    }


def extract_tool_input(data: dict, tool_name: str) -> Optional[dict]:
    """قراءة مدخلات الأداة من الرد (قد تسبقها كتل thinking)"""
    for block in data.get("content", []) or []:
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            return block.get("input") or {}
    return None


def _record(ok: bool, code=None, message: str = ""):
    last_status.update({"ok": ok, "code": code, "message": message[:300],
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


async def call_tool(system: str, content: list, tool: dict, use_vision: bool = False, max_tokens: int = 2000) -> Optional[dict]:
    """
    استدعاء النموذج وإلزامه بالإجابة عبر الأداة المحددة.
    يعيد مدخلات الأداة (dict) أو None عند الفشل.
    """
    if not ANTHROPIC_API_KEY:
        return None
    model = ANTHROPIC_VISION_MODEL if use_vision else ANTHROPIC_TEXT_MODEL
    payload = build_payload(system, content, tool, model, max_tokens)
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    attempts = 1 if forced_tool_supported(model) else 2  # مع auto قد لا يستدعي النموذج الأداة: نعيد مرة واحدة
    for _ in range(attempts):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(ANTHROPIC_URL, json=payload, headers=headers,
                                         timeout=90.0 if use_vision else 45.0)
            if resp.status_code != 200:
                logger.error(f"Anthropic API {resp.status_code}: {resp.text[:300]}")
                _record(False, resp.status_code, resp.text)
                return None
            data = resp.json()
            usage = data.get("usage") or {}
            logger.info(f"Anthropic OK model={model} in={usage.get('input_tokens')} "
                        f"cache_read={usage.get('cache_read_input_tokens')} cache_write={usage.get('cache_creation_input_tokens')} "
                        f"out={usage.get('output_tokens')} stop={data.get('stop_reason')}")
            _record(True, 200)
            if data.get("stop_reason") == "refusal":
                logger.warning(f"Anthropic refusal: {data.get('stop_details')}")
                return None
            result = extract_tool_input(data, tool["name"])
            if result is not None:
                return result
            logger.warning(f"Anthropic response contained no tool_use block (stop={data.get('stop_reason')})")
        except Exception as e:
            logger.error(f"Anthropic call failed: {e}")
            _record(False, None, str(e))
            return None
    return None


CHECK_TOOL = {
    "name": "ping",
    "description": "Confirm the connection.",
    "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
}


async def check_connection() -> dict:
    """اختبار سريع للاتصال بـ Claude من لوحة التحكم (يكشف المفتاح غير الصالح وأخطاء الإعداد)"""
    if not ANTHROPIC_API_KEY:
        return {"ok": False, "code": None, "message": "ANTHROPIC_API_KEY غير مضبوط"}
    result = await call_tool("Connection test.", [{"type": "text", "text": "Call the ping tool with ok=true."}],
                             CHECK_TOOL, use_vision=False, max_tokens=200)
    status = dict(last_status)
    status["ok"] = result is not None and bool(status.get("ok"))
    return status

"""
ai_client.py - الاتصال بـ Claude (Anthropic Messages API) لفهم رسائل الطلبات

- نموذج سريع للرسائل النصية، ونموذج أقوى لقراءة صور الجداول وملفات PDF.
- المخرجات منظمة دائماً عبر "أداة" إلزامية (tool_choice) بدلاً من نص حر يحتاج تحليلاً.
"""

import base64
import logging
import os
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


async def call_tool(system: str, content: list, tool: dict, use_vision: bool = False, max_tokens: int = 2000) -> Optional[dict]:
    """
    استدعاء النموذج مع إلزامه بالإجابة عبر الأداة المحددة.
    يعيد مدخلات الأداة (dict) أو None عند الفشل.
    """
    if not ANTHROPIC_API_KEY:
        return None
    payload = {
        "model": ANTHROPIC_VISION_MODEL if use_vision else ANTHROPIC_TEXT_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
        "messages": [{"role": "user", "content": content}],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(ANTHROPIC_URL, json=payload, headers=headers, timeout=60.0 if use_vision else 25.0)
        if resp.status_code != 200:
            logger.error(f"Anthropic API {resp.status_code}: {resp.text[:300]}")
            return None
        for block in resp.json().get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == tool["name"]:
                return block.get("input") or {}
        logger.error("Anthropic response contained no tool_use block")
    except Exception as e:
        logger.error(f"Anthropic call failed: {e}")
    return None

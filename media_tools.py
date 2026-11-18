"""
media_tools.py - استخراج النص من الملفات والرسائل الصوتية

- Word (.docx) و Excel (.xlsx) و CSV/نص و PDF (نصي) تُحوَّل إلى نص محلياً.
- PDF الممسوح ضوئياً والصور تُقرأ بـ Claude (ai_client) عند الحاجة.
- الرسائل الصوتية تُحوَّل إلى نص عبر Whisper Large v3 على Together AI (TOGETHER_API_KEY).
"""

import base64
import io
import logging
import os
import re
import zipfile
from typing import Optional

import httpx

logger = logging.getLogger("SalesCRM.media")

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_TRANSCRIBE_URL = "https://api.together.ai/v1/audio/transcriptions"
WHISPER_MODEL = os.getenv("TRANSCRIBE_MODEL", "openai/whisper-large-v3")

DOCX_MIMES = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
XLSX_MIMES = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
TEXT_MIMES = {"text/plain", "text/csv", "application/csv"}


def kind_of(mimetype: str, filename: str = "") -> str:
    """تصنيف الملف: image | pdf | docx | xlsx | text | audio | other"""
    mt = (mimetype or "").split(";")[0].strip().lower()
    fn = (filename or "").lower()
    if mt.startswith("image/"):
        return "image"
    if mt.startswith("audio/"):
        return "audio"
    if mt == "application/pdf" or fn.endswith(".pdf"):
        return "pdf"
    if mt in DOCX_MIMES or fn.endswith(".docx"):
        return "docx"
    if mt in XLSX_MIMES or fn.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if mt in TEXT_MIMES or fn.endswith((".txt", ".csv", ".md")):
        return "text"
    return "other"


def docx_to_text(raw: bytes) -> str:
    """قراءة Word بدون مكتبات إضافية: الفقرات والجداول (الخلايا مفصولة بـ |)"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"DOCX read failed: {e}")
        return ""
    # داخل خلية الجدول: الفقرات تُدمج بمسافة حتى يبقى الصف (المنتج | السعر) في سطر واحد
    xml = re.sub(r"<w:tc\b.*?</w:tc>", lambda mt: re.sub(r"</w:p>", " ", mt.group(0)), xml, flags=re.S)
    xml = re.sub(r"</w:tc>", " | ", xml)
    xml = re.sub(r"</w:p>|</w:tr>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'"))
    lines = [re.sub(r"[ \t]+", " ", ln).strip(" |") for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def xlsx_to_text(raw: bytes, max_rows: int = 400, max_cols: int = 25) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets[:5]:
            out.append(f"### Sheet: {ws.title}")
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if r >= max_rows:
                    break
                cells = ["" if v is None else str(v).strip() for v in row[:max_cols]]
                if any(cells):
                    out.append(" | ".join(cells))
        return "\n".join(out)
    except Exception as e:
        logger.warning(f"XLSX read failed: {e}")
        return ""


def pdf_to_text(raw: bytes, max_pages: int = 60) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages]).strip()
    except Exception as e:
        logger.warning(f"PDF text extraction failed: {e}")
        return ""


def extract_text_locally(raw: bytes, mimetype: str, filename: str = "") -> str:
    k = kind_of(mimetype, filename)
    if k == "docx":
        return docx_to_text(raw)
    if k == "xlsx":
        return xlsx_to_text(raw)
    if k == "pdf":
        return pdf_to_text(raw)
    if k == "text":
        for enc in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
    return ""


def can_transcribe() -> bool:
    return bool(TOGETHER_API_KEY)


async def transcribe_audio(media_b64: str, mimetype: str) -> Optional[str]:
    """تحويل الرسالة الصوتية إلى نص (Whisper يكتشف العربية والإنجليزية تلقائياً)"""
    if not TOGETHER_API_KEY:
        return None
    mt = (mimetype or "audio/ogg").split(";")[0].strip()
    ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/aac": "aac",
           "audio/wav": "wav", "audio/webm": "webm"}.get(mt, "ogg")
    try:
        audio = base64.b64decode(media_b64)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TOGETHER_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
                data={"model": WHISPER_MODEL, "response_format": "json"},
                files={"file": (f"voice.{ext}", audio, mt)},
                timeout=60.0,
            )
        if resp.status_code != 200:
            logger.error(f"Transcription API {resp.status_code}: {resp.text[:300]}")
            return None
        return (resp.json().get("text") or "").strip() or None
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return None

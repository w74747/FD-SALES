"""
ai_client.py - الاتصال بنماذج الذكاء الاصطناعي لفهم الطلبات والرد على الاستفسارات

- عدة مزوّدين بالترتيب (AI_PROVIDERS، الافتراضي: deepseek ثم anthropic ثم together):
  إذا فشل مزوّد (مفتاح غير صالح، رصيد، ضغط، عطل) يُجرَّب التالي تلقائياً.
- DeepSeek (deepseek-flash): الأرخص مع دعم الأدوات والصور وسياق مليون token — الخيار الأول.
- المخرجات منظمة دائماً عبر "أداة" (tool) بدلاً من نص حر يحتاج تحليلاً.
- بقية الكود يمرّر الطلب بصيغة Anthropic (كتل text / image / document)، وتُحوَّل هنا لصيغة OpenAI عند الحاجة.
"""

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("SalesCRM.ai")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_TEXT_MODEL = os.getenv("ANTHROPIC_TEXT_MODEL", "claude-haiku-5-5")
ANTHROPIC_VISION_MODEL = os.getenv("ANTHROPIC_VISION_MODEL", "claude-sonnet-5-5")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# kind: anthropic = Messages API؛ openai = chat/completions المتوافق مع OpenAI
PROVIDERS = {
    "deepseek": {
        "kind": "openai", "label": "DeepSeek", "key_env": "DEEPSEEK_API_KEY",
        "url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions",
        "text_model": os.getenv("DEEPSEEK_TEXT_MODEL", "deepseek-flash"),
        "vision_model": os.getenv("DEEPSEEK_VISION_MODEL", "deepseek-flash"),
    },
    "anthropic": {
        "kind": "anthropic", "label": "Claude", "key_env": "ANTHROPIC_API_KEY",
        "url": ANTHROPIC_URL, "text_model": ANTHROPIC_TEXT_MODEL, "vision_model": ANTHROPIC_VISION_MODEL,
    },
    "together": {
        "kind": "openai", "label": "Together", "key_env": "TOGETHER_API_KEY",
        "url": os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1").rstrip("/") + "/chat/completions",
        "text_model": os.getenv("TOGETHER_TEXT_MODEL", "Qwen/Qwen3.8-Flash"),
        "vision_model": os.getenv("TOGETHER_VISION_MODEL", "Qwen/Qwen3.5-9B"),
    },
}
PROVIDER_ORDER = [p.strip() for p in os.getenv("AI_PROVIDERS", "deepseek,anthropic,together").split(",")
                  if p.strip() in PROVIDERS]

# النماذج التي ترفض إجبار الأداة (tool_choice: tool/any) في Anthropic — نستخدم معها auto مع تعليمات وإعادة محاولة
_NO_FORCED_TOOL_PREFIXES = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-fable-5-2",
                            "claude-mythos-5-1", "claude-mythos-5-2")

# آخر نتيجة اتصال لكل مزوّد (تُعرض في لوحة التحكم بدل افتراض أن وجود المفتاح يعني أنه يعمل)
provider_status = {name: {"ok": None, "code": None, "message": "", "at": None, "model": ""} for name in PROVIDERS}
last_status = {"ok": None, "code": None, "message": "", "at": None, "provider": None}


def provider_key(name: str) -> str:
    return os.getenv(PROVIDERS[name]["key_env"], "").strip()


def configured_providers() -> list:
    return [p for p in PROVIDER_ORDER if provider_key(p)]


def is_enabled() -> bool:
    return bool(configured_providers())


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


# ----------------------------- Anthropic -----------------------------

def forced_tool_supported(model: str) -> bool:
    return not (model or "").startswith(_NO_FORCED_TOOL_PREFIXES)


def build_payload(system: str, content: list, tool: dict, model: str, max_tokens: int) -> dict:
    """
    طلب Anthropic حسب قدرات النموذج:
    - لا نرسل temperature: Haiku 5.5 / Sonnet 5.5 ترفض أي قيمة غير الافتراضية بخطأ 400.
    - النظام (system) ككتلة مع cache_control حتى يُخزَّن مؤقتاً.
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
    """قراءة مدخلات الأداة من رد Anthropic (قد تسبقها كتل thinking)"""
    for block in data.get("content", []) or []:
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            return block.get("input") or {}
    return None


# ----------------------------- OpenAI-compatible (DeepSeek / Together) -----------------------------

def to_openai_content(content: list) -> Optional[list]:
    """
    تحويل كتل Anthropic إلى صيغة OpenAI. PDF غير مدعوم في هذه الواجهة → None (يُجرَّب المزوّد التالي).
    cache_control يُحذف: DeepSeek يخزّن البادئة المتكررة تلقائياً (قاعدة المعرفة تأتي أولاً لهذا السبب).
    """
    out = []
    for b in content or []:
        t = b.get("type")
        if t == "text":
            out.append({"type": "text", "text": b.get("text", "")})
        elif t == "image":
            src = b.get("source") or {}
            out.append({"type": "image_url",
                        "image_url": {"url": f"data:{src.get('media_type')};base64,{src.get('data', '')}"}})
        else:
            return None
    return out


def build_openai_payload(system: str, content: list, tool: dict, model: str, max_tokens: int,
                         forced: bool = True) -> Optional[dict]:
    parts = to_openai_content(content)
    if parts is None:
        return None
    has_image = any(p["type"] == "image_url" for p in parts)
    user_content = parts if has_image else "\n\n".join(p["text"] for p in parts)
    sys_text = system + (f"\n\nYou MUST respond by calling the `{tool['name']}` function exactly once." if not forced else "")
    return {
        "model": model,
        "max_tokens": max(max_tokens, 2000),
        "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": user_content}],
        "tools": [{"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""),
                                                    "parameters": tool["input_schema"]}}],
        "tool_choice": {"type": "function", "function": {"name": tool["name"]}} if forced else "auto",
    }


def _loads_lenient(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", text, re.S)  # JSON داخل نص أو ```json
        if m:
            try:
                val = json.loads(m.group(0))
                return val if isinstance(val, dict) else None
            except ValueError:
                return None
    return None


def extract_openai_tool_input(data: dict, tool_name: str) -> Optional[dict]:
    """مدخلات الدالة من رد chat/completions؛ وإن رد النموذج بـ JSON نصي نقبله أيضاً"""
    try:
        message = (data.get("choices") or [{}])[0].get("message") or {}
    except (AttributeError, IndexError):
        return None
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        if fn.get("name") == tool_name:
            args = fn.get("arguments")
            return args if isinstance(args, dict) else (_loads_lenient(args) or {})
    return _loads_lenient(message.get("content") or "")


# ----------------------------- الاستدعاء -----------------------------

def _record(provider: str, ok: bool, code=None, message: str = "", model: str = ""):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    provider_status[provider].update({"ok": ok, "code": code, "message": (message or "")[:300], "at": stamp, "model": model})
    last_status.update({"ok": ok, "code": code, "message": (message or "")[:300], "at": stamp, "provider": provider})


async def _post(url: str, payload: dict, headers: dict, timeout: float):
    async with httpx.AsyncClient() as client:
        return await client.post(url, json=payload, headers=headers, timeout=timeout)


async def _call_anthropic(system, content, tool, use_vision, max_tokens) -> Optional[dict]:
    cfg = PROVIDERS["anthropic"]
    model = cfg["vision_model"] if use_vision else cfg["text_model"]
    payload = build_payload(system, content, tool, model, max_tokens)
    headers = {"x-api-key": provider_key("anthropic"), "anthropic-version": "2023-06-01", "content-type": "application/json"}
    for _ in range(1 if forced_tool_supported(model) else 2):  # مع auto قد لا يستدعي النموذج الأداة: نعيد مرة
        resp = await _post(cfg["url"], payload, headers, 90.0 if use_vision else 45.0)
        if resp.status_code != 200:
            logger.error(f"[AI anthropic] {resp.status_code}: {resp.text[:300]}")
            _record("anthropic", False, resp.status_code, resp.text, model)
            return None
        data = resp.json()
        usage = data.get("usage") or {}
        logger.info(f"[AI anthropic] OK model={model} in={usage.get('input_tokens')} "
                    f"cache_read={usage.get('cache_read_input_tokens')} out={usage.get('output_tokens')}")
        _record("anthropic", True, 200, "", model)
        if data.get("stop_reason") == "refusal":
            return None
        result = extract_tool_input(data, tool["name"])
        if result is not None:
            return result
        logger.warning("[AI anthropic] no tool_use block in response")
    return None


async def _call_openai(name, system, content, tool, use_vision, max_tokens) -> Optional[dict]:
    cfg = PROVIDERS[name]
    model = cfg["vision_model"] if use_vision else cfg["text_model"]
    payload = build_openai_payload(system, content, tool, model, max_tokens, forced=True)
    if payload is None:
        logger.info(f"[AI {name}] content type not supported (PDF) — trying next provider")
        return None
    headers = {"Authorization": f"Bearer {provider_key(name)}", "Content-Type": "application/json"}
    resp = await _post(cfg["url"], payload, headers, 90.0 if use_vision else 45.0)
    if resp.status_code == 400 and "tool_choice" in resp.text:
        # بعض النماذج لا تقبل تحديد الدالة إجبارياً: auto + تعليمات صريحة
        payload = build_openai_payload(system, content, tool, model, max_tokens, forced=False)
        resp = await _post(cfg["url"], payload, headers, 90.0 if use_vision else 45.0)
    if resp.status_code != 200:
        logger.error(f"[AI {name}] {resp.status_code}: {resp.text[:300]}")
        _record(name, False, resp.status_code, resp.text, model)
        return None
    data = resp.json()
    usage = data.get("usage") or {}
    logger.info(f"[AI {name}] OK model={model} in={usage.get('prompt_tokens')} "
                f"cache_hit={usage.get('prompt_cache_hit_tokens')} out={usage.get('completion_tokens')}")
    _record(name, True, 200, "", model)
    result = extract_openai_tool_input(data, tool["name"])
    if result is None:
        logger.warning(f"[AI {name}] no function call in response")
    return result


async def call_tool(system: str, content: list, tool: dict, use_vision: bool = False, max_tokens: int = 2000,
                    providers: Optional[list] = None) -> Optional[dict]:
    """
    استدعاء النموذج وإلزامه بالإجابة عبر الأداة المحددة، مع الانتقال للمزوّد التالي عند الفشل.
    يعيد مدخلات الأداة (dict) أو None إذا فشل كل المزوّدين.
    """
    for name in providers or configured_providers():
        if not provider_key(name):
            continue
        started = time.monotonic()
        try:
            if PROVIDERS[name]["kind"] == "anthropic":
                result = await _call_anthropic(system, content, tool, use_vision, max_tokens)
            else:
                result = await _call_openai(name, system, content, tool, use_vision, max_tokens)
        except Exception as e:
            logger.error(f"[AI {name}] call failed: {e}")
            _record(name, False, None, str(e))
            result = None
        if result is not None:
            logger.info(f"[AI] answered by {name} in {time.monotonic() - started:.1f}s")
            return result
    return None


CHECK_TOOL = {
    "name": "ping",
    "description": "Confirm the connection.",
    "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
}


async def check_connection() -> dict:
    """اختبار فعلي لكل مزوّد مضبوط (يكشف المفتاح غير الصالح وأخطاء الإعداد) — تكلفته أجزاء من السنت"""
    results = []
    for name in PROVIDER_ORDER:
        cfg = PROVIDERS[name]
        if not provider_key(name):
            results.append({"provider": name, "label": cfg["label"], "configured": False, "ok": False,
                            "code": None, "message": f"{cfg['key_env']} غير مضبوط", "model": cfg["text_model"]})
            continue
        started = time.monotonic()
        result = await call_tool("Connection test.", [{"type": "text", "text": "Call the ping tool with ok=true."}],
                                 CHECK_TOOL, use_vision=False, max_tokens=200, providers=[name])
        st = provider_status[name]
        results.append({"provider": name, "label": cfg["label"], "configured": True,
                        "ok": result is not None and bool(st.get("ok")), "code": st.get("code"),
                        "message": st.get("message", ""), "model": st.get("model") or cfg["text_model"],
                        "seconds": round(time.monotonic() - started, 1)})
    active = next((r["provider"] for r in results if r["ok"]), None)
    return {"ok": active is not None, "active": active, "providers": results}


def status_summary() -> dict:
    """للوحة التحكم: ترتيب المزوّدين، أيها مضبوط، وآخر نتيجة لكل واحد"""
    return {"order": PROVIDER_ORDER,
            "providers": [{"provider": n, "label": PROVIDERS[n]["label"], "configured": bool(provider_key(n)),
                           "model": PROVIDERS[n]["text_model"], **{k: provider_status[n][k] for k in ("ok", "code", "at")}}
                          for n in PROVIDER_ORDER],
            "last": dict(last_status)}

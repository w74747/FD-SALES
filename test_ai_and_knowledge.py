"""اختبارات استدعاء نماذج الذكاء الاصطناعي (ai_client: DeepSeek / Claude / Together) وحدود قاعدة المعرفة (knowledge) — بدون اتصال بالإنترنت"""

import asyncio

import ai_client
import knowledge

TOOL = {"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}


# ----------------------------- ai_client -----------------------------

def test_no_sampling_params_ever():
    # Haiku 5.5 و Sonnet 5.5 ترفضان temperature غير الافتراضية بخطأ 400
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
        payload = ai_client.build_payload("sys", [{"type": "text", "text": "x"}], TOOL, model, 800)
        assert "temperature" not in payload and "top_p" not in payload and "top_k" not in payload


def test_haiku_keeps_forced_tool():
    payload = ai_client.build_payload("sys", [], TOOL, "claude-haiku-5-5", 800)
    assert payload["tool_choice"] == {"type": "tool", "name": "t"}
    assert payload["max_tokens"] == 800
    assert payload["system"][0]["text"] == "sys"


def test_sonnet_55_uses_auto_with_instruction():
    # Sonnet 5.5 ترفض إجبار الأداة: auto + تعليمات + مساحة للتفكير
    payload = ai_client.build_payload("sys", [], TOOL, "claude-sonnet-5-5", 2000)
    assert payload["tool_choice"] == {"type": "auto"}
    assert "`t` tool exactly once" in payload["system"][0]["text"]
    assert payload["max_tokens"] >= 8000


def test_system_is_cacheable_block():
    payload = ai_client.build_payload("sys", [], TOOL, "claude-haiku-5-5", 800)
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_extract_tool_input_skips_thinking_blocks():
    data = {"content": [{"type": "thinking", "thinking": "", "signature": "x"},
                        {"type": "text", "text": "hi"},
                        {"type": "tool_use", "name": "t", "input": {"a": 1}}]}
    assert ai_client.extract_tool_input(data, "t") == {"a": 1}
    assert ai_client.extract_tool_input({"content": [{"type": "text", "text": "no tool"}]}, "t") is None


class Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._d, self.text = status, data or {}, text

    def json(self):
        return self._d


def fake_post(monkeypatch, handler):
    """handler(url, payload) -> Resp ؛ يسجّل كل الاستدعاءات"""
    calls = []

    async def _post(url, payload, headers, timeout):
        calls.append((url, payload, headers))
        return handler(url, payload)

    monkeypatch.setattr(ai_client, "_post", _post)
    return calls


def only_keys(monkeypatch, **keys):
    for name, cfg in ai_client.PROVIDERS.items():
        monkeypatch.setenv(cfg["key_env"], keys.get(name, ""))


def test_default_order_puts_cheapest_first():
    assert ai_client.PROVIDER_ORDER[0] == "deepseek"
    assert ai_client.PROVIDERS["deepseek"]["text_model"] == "deepseek-flash"
    assert ai_client.PROVIDERS["deepseek"]["vision_model"] == "deepseek-flash"


def test_call_tool_records_401_and_falls_back(monkeypatch):
    only_keys(monkeypatch, anthropic="bad", deepseek="good")
    monkeypatch.setattr(ai_client, "PROVIDER_ORDER", ["anthropic", "deepseek"])

    def handler(url, payload):
        if "anthropic" in url:
            return Resp(401, text='{"error":{"type":"authentication_error","message":"invalid x-api-key"}}')
        return Resp(200, {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "t", "arguments": '{"a": 1}'}}]}}]})

    calls = fake_post(monkeypatch, handler)
    assert asyncio.run(ai_client.call_tool("s", [{"type": "text", "text": "x"}], TOOL)) == {"a": 1}
    assert ai_client.provider_status["anthropic"]["code"] == 401
    assert ai_client.provider_status["deepseek"]["ok"] is True
    assert len(calls) == 2


def test_all_providers_fail_returns_none(monkeypatch):
    only_keys(monkeypatch, deepseek="k", together="k")
    fake_post(monkeypatch, lambda url, p: Resp(500, text="down"))
    assert asyncio.run(ai_client.call_tool("s", [{"type": "text", "text": "x"}], TOOL)) is None


def test_openai_payload_shape():
    content = [{"type": "text", "text": "KB", "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": "Q"}]
    p = ai_client.build_openai_payload("sys", content, TOOL, "deepseek-flash", 800)
    assert p["messages"][0] == {"role": "system", "content": "sys"}
    assert p["messages"][1]["content"] == "KB\n\nQ"  # قاعدة المعرفة أولاً ليستفيد من التخزين التلقائي
    assert p["tools"][0]["function"]["name"] == "t"
    assert p["tool_choice"] == {"type": "function", "function": {"name": "t"}}
    assert "temperature" not in p


def test_openai_payload_image_and_pdf():
    img = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
           {"type": "text", "text": "read"}]
    p = ai_client.build_openai_payload("s", img, TOOL, "deepseek-flash", 800)
    assert p["messages"][1]["content"][0] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    pdf = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "x"}}]
    assert ai_client.build_openai_payload("s", pdf, TOOL, "deepseek-flash", 800) is None


def test_pdf_skips_openai_providers_and_uses_claude(monkeypatch):
    only_keys(monkeypatch, deepseek="k", anthropic="k")
    monkeypatch.setattr(ai_client, "PROVIDER_ORDER", ["deepseek", "anthropic"])
    calls = fake_post(monkeypatch, lambda url, p: Resp(200, {"content": [
        {"type": "tool_use", "name": "t", "input": {"ok": True}}]}))
    pdf = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "x"}}]
    assert asyncio.run(ai_client.call_tool("s", pdf, TOOL)) == {"ok": True}
    assert len(calls) == 1 and "anthropic" in calls[0][0]


def test_tool_choice_rejected_retries_with_auto(monkeypatch):
    only_keys(monkeypatch, together="k")
    monkeypatch.setattr(ai_client, "PROVIDER_ORDER", ["together"])

    def handler(url, payload):
        if payload["tool_choice"] != "auto":
            return Resp(400, text='{"error": "tool_choice not supported"}')
        return Resp(200, {"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}]})

    calls = fake_post(monkeypatch, handler)
    assert asyncio.run(ai_client.call_tool("s", [{"type": "text", "text": "x"}], TOOL)) == {"ok": True}
    assert len(calls) == 2
    assert "exactly once" in calls[1][1]["messages"][0]["content"]


def test_anthropic_retries_once_when_auto_skips_tool(monkeypatch):
    only_keys(monkeypatch, anthropic="k")
    monkeypatch.setattr(ai_client, "PROVIDER_ORDER", ["anthropic"])
    monkeypatch.setitem(ai_client.PROVIDERS["anthropic"], "vision_model", "claude-sonnet-5-5")
    replies = [{"content": [{"type": "text", "text": "plain"}], "stop_reason": "end_turn"},
               {"content": [{"type": "tool_use", "name": "t", "input": {"ok": True}}], "stop_reason": "tool_use"}]
    calls = fake_post(monkeypatch, lambda url, p: Resp(200, replies[len(calls) - 1]))
    assert asyncio.run(ai_client.call_tool("s", [], TOOL, use_vision=True)) == {"ok": True}
    assert len(calls) == 2


def test_check_connection_reports_each_provider(monkeypatch):
    only_keys(monkeypatch, anthropic="bad", deepseek="good")
    monkeypatch.setattr(ai_client, "PROVIDER_ORDER", ["deepseek", "anthropic", "together"])

    def handler(url, payload):
        if "anthropic" in url:
            return Resp(401, text="invalid x-api-key")
        return Resp(200, {"choices": [{"message": {"tool_calls": [{"function": {"name": "ping", "arguments": {"ok": True}}}]}}]})

    fake_post(monkeypatch, handler)
    r = asyncio.run(ai_client.check_connection())
    by = {p["provider"]: p for p in r["providers"]}
    assert r["ok"] is True and r["active"] == "deepseek"
    assert by["deepseek"]["ok"] is True
    assert by["anthropic"]["ok"] is False and by["anthropic"]["code"] == 401
    assert by["together"]["configured"] is False


def test_is_enabled_with_any_key(monkeypatch):
    only_keys(monkeypatch)
    assert ai_client.is_enabled() is False
    only_keys(monkeypatch, together="k")
    assert ai_client.is_enabled() is True


# ----------------------------- knowledge -----------------------------

def test_assemble_reports_truncation_instead_of_silent_cut():
    text, rep = knowledge.assemble([("A", "a" * 50), ("B", "b" * 50), ("C", "c" * 50)], limit=120)
    statuses = [s["status"] for s in rep["sections"]]
    assert statuses == ["full", "truncated", "excluded"]
    assert rep["complete"] is False
    assert len(text) <= 120
    assert rep["used"] == len(text)


def test_assemble_everything_fits():
    text, rep = knowledge.assemble([("A", "x"), ("B", "y")], limit=1000)
    assert rep["complete"] is True
    assert "=== A ===\nx" in text and "=== B ===\ny" in text


def test_default_limits_are_raised():
    assert knowledge.MAX_CONTEXT_CHARS >= 200000
    assert knowledge.MAX_DOC_CHARS >= 150000


def test_knowledge_block_is_cached_and_before_question():
    blocks = knowledge.knowledge_blocks("FACTS")
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"].endswith("FACTS")


def test_answer_direct_sends_kb_first_and_no_prices_rule(monkeypatch):
    seen = {}

    async def fake_call(system, content, tool, use_vision=False, max_tokens=0):
        seen.update(system=system, content=content, tool=tool)
        return {"needs_reply": True, "can_answer": True, "answer": "ok"}

    monkeypatch.setattr(knowledge.ai_client, "call_tool", fake_call)
    out = asyncio.run(knowledge.answer_direct("KB", "كم السعر؟", "", "ar"))
    assert out["answer"] == "ok"
    assert seen["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Never quote any price" in seen["system"]
    assert "reply in Arabic" in seen["content"][1]["text"]


def test_answer_direct_empty_kb_escalates_without_calling_claude(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("should not call Claude")
    monkeypatch.setattr(knowledge.ai_client, "call_tool", boom)
    out = asyncio.run(knowledge.answer_direct("  ", "hi", "", "en"))
    assert out["can_answer"] is False

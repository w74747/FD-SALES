"""اختبارات طريقة استدعاء Claude (ai_client) وحدود قاعدة المعرفة (knowledge) — بدون اتصال بالإنترنت"""

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


def test_call_tool_records_401(monkeypatch):
    class Resp:
        status_code = 401
        text = '{"error":{"type":"authentication_error","message":"invalid x-api-key"}}'

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return Resp()

    monkeypatch.setattr(ai_client, "ANTHROPIC_API_KEY", "bad")
    monkeypatch.setattr(ai_client.httpx, "AsyncClient", lambda: Client())
    assert asyncio.run(ai_client.call_tool("s", [], TOOL)) is None
    assert ai_client.last_status["ok"] is False and ai_client.last_status["code"] == 401


def test_call_tool_retries_once_when_auto_skips_tool(monkeypatch):
    calls = []

    class Resp:
        status_code = 200
        text = ""

        def __init__(self, data): self._d = data
        def json(self): return self._d

    replies = [{"content": [{"type": "text", "text": "plain"}], "stop_reason": "end_turn"},
               {"content": [{"type": "tool_use", "name": "t", "input": {"ok": True}}], "stop_reason": "tool_use"}]

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **k):
            calls.append(json)
            return Resp(replies[len(calls) - 1])

    monkeypatch.setattr(ai_client, "ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ai_client, "ANTHROPIC_VISION_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(ai_client.httpx, "AsyncClient", lambda: Client())
    assert asyncio.run(ai_client.call_tool("s", [], TOOL, use_vision=True)) == {"ok": True}
    assert len(calls) == 2


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

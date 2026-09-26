"""Тесты конвертации формата для нативного Anthropic-клиента (без сети)."""

from __future__ import annotations

from core.llm.anthropic_client import (
    _tools_to_anthropic,
    is_anthropic_endpoint,
    to_anthropic_messages,
)


def test_endpoint_detection():
    assert is_anthropic_endpoint("https://api.anthropic.com/v1")
    assert not is_anthropic_endpoint("https://openrouter.ai/api/v1")
    assert not is_anthropic_endpoint("")


def test_system_extracted_separately():
    system, msgs = to_anthropic_messages([
        {"role": "system", "content": "Ты помощник."},
        {"role": "user", "content": "Привет"},
    ])
    assert system == "Ты помощник."
    assert msgs == [{"role": "user", "content": "Привет"}]


def test_assistant_tool_calls_become_tool_use():
    _, msgs = to_anthropic_messages([
        {"role": "user", "content": "сделай"},
        {"role": "assistant", "content": "работаю",
         "tool_calls": [{"id": "t1", "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}}]},
    ])
    asst = msgs[1]
    assert asst["role"] == "assistant"
    kinds = [b["type"] for b in asst["content"]]
    assert kinds == ["text", "tool_use"]
    tu = asst["content"][1]
    assert tu["id"] == "t1" and tu["name"] == "read_file" and tu["input"] == {"path": "a.txt"}


def test_tool_results_merged_into_one_user_turn():
    _, msgs = to_anthropic_messages([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "f", "arguments": "{}"}},
            {"id": "t2", "function": {"name": "g", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "tool", "tool_call_id": "t2", "content": "r2"},
    ])
    # Оба результата — в одном user-ходе, блоками tool_result.
    results = msgs[-1]
    assert results["role"] == "user"
    ids = [b["tool_use_id"] for b in results["content"]]
    assert ids == ["t1", "t2"]
    assert results["content"][0]["type"] == "tool_result"


def test_bad_tool_json_is_safe():
    _, msgs = to_anthropic_messages([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "f", "arguments": "не-json"}},
        ]},
    ])
    assert msgs[0]["content"][0]["input"] == {}  # кривой JSON → пустой ввод


def test_tools_schema_converted():
    tools = [{"type": "function", "function": {
        "name": "search", "description": "ищет", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
    }}]
    out = _tools_to_anthropic(tools)
    assert out == [{"name": "search", "description": "ищет", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}]


def test_multimodal_text_parts_flattened():
    system, msgs = to_anthropic_messages([
        {"role": "user", "content": [{"type": "text", "text": "часть1"}, {"type": "text", "text": "часть2"}]},
    ])
    assert msgs[0]["content"] == "часть1часть2"

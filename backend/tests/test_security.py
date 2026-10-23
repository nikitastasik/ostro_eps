"""Регрессии по ревью безопасности: статика без выхода из папки, устойчивость к ответу LLM, ответ 500 без деталей."""

import asyncio
import json
from types import SimpleNamespace

from app import llm
from app.agents import concierge
from app.knowledge.registry import load_builtin
from app.main import safe_static_file, unhandled


def test_static_stays_inside_dir(tmp_path):
    base = tmp_path / "static"
    (base / "assets").mkdir(parents=True)
    (base / "index.html").write_text("<html></html>")
    (base / "assets" / "app.js").write_text("// js")
    (tmp_path / "secret.txt").write_text("SECRET_KEY=x")

    assert safe_static_file(base, "assets/app.js") == (base / "assets" / "app.js").resolve()
    assert safe_static_file(base, "../secret.txt") is None
    assert safe_static_file(base, "assets/../../secret.txt") is None
    assert safe_static_file(base, str(tmp_path / "secret.txt")) is None  # абсолютный путь
    assert safe_static_file(base, "assets") is None  # папка, не файл
    assert safe_static_file(base, "") is None
    assert safe_static_file(base, "assets/\x00app.js") is None


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeClient:
    content = "[]"

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **kw):
        return _FakeResponse(self.content)


def test_chat_json_returns_only_objects(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm.httpx, "Client", _FakeClient)
    _FakeClient.content = '[{"key": "quietness"}]'
    assert llm.chat_json("s", "u") is None
    _FakeClient.content = '{"criteria": []}'
    assert llm.chat_json("s", "u") == {"criteria": []}


def test_understand_falls_back_to_rules_on_malformed_llm(monkeypatch):
    specs = {s["key"]: s for s in load_builtin()}
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"criteria": "мусор", "unmatched": 5})
    parsed = concierge.understand("тихий номер с видом на закат", specs)
    assert parsed.parser == "rules" and parsed.criteria


def test_500_does_not_leak_exception_text():
    resp = asyncio.run(
        unhandled(SimpleNamespace(url=SimpleNamespace(path="/api/x")), RuntimeError("password=hotelier123 at /app/secret"))
    )
    assert resp.status_code == 500
    body = json.loads(resp.body)
    assert "hotelier123" not in body["detail"] and "RuntimeError" not in body["detail"]


def test_compiler_rejects_malformed_llm_spec(monkeypatch):
    from app.agents import compiler

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"title": "x", "value_fields": {}, "recipe": ["не объект"]})
    assert compiler.compile_llm("есть ли аптека рядом") is None

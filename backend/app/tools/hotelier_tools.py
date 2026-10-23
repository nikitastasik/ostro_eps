"""Инструмент агента-интервьюера: вопрос отельеру."""

from __future__ import annotations

from .base import ToolResult, tool

AGENT = "Интервьюер"


@tool("hotelier.ask", "Вопрос отельеру", AGENT, ["отельер (кабинет)"],
      "Формулирует вопрос отельеру и ждёт ответа в свободной форме; ответ раскладывается по полям характеристики.",
      generic=True, params_schema={"question": "текст вопроса", "fields": "поля ответа"})
def ask(ctx, params) -> ToolResult:
    from ..agents.interviewer import ask_hotelier

    spec = params["spec"]
    q, created = ask_hotelier(ctx.session, ctx.hotel, spec, reason=params.get("reason", "missing"),
                              hypothesis=params.get("hypothesis"), run_id=params.get("run_id"))
    note = "вопрос отправлен отельеру" if created else "вопрос уже ждёт ответа отельера"
    return ToolResult(None, f"Спросили отельера: «{q.text}»", 0.0, "hotelier", "кабинет отельера", status="asked",
                      note=note, evidence={"question_id": q.id})

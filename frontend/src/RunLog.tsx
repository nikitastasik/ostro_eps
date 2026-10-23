import { useEffect, useRef, useState } from "react";
import { api, Run, Step } from "./api";
import { Spinner, fmtDate } from "./ui";

const KIND_RU: Record<string, string> = {
  plan: "план", tool: "инструмент", decision: "решение", result: "результат", question: "вопрос",
  conflict: "противоречие", llm: "LLM", error: "ошибка",
};

export function Steps({ steps, filter }: { steps: Step[]; filter?: string | null }) {
  const list = filter ? steps.filter((s) => s.char_key === filter || !s.char_key) : steps;
  if (!list.length) return <div className="empty small">Шагов пока нет</div>;
  return (
    <div className="log">
      {list.map((s) => (
        <div key={s.idx} className={`st ${s.kind}`}>
          <div>
            <div className="who">{s.agent}</div>
            <div className="tiny muted">{KIND_RU[s.kind] || s.kind}{s.duration_ms != null ? ` · ${s.duration_ms} мс` : ""}</div>
          </div>
          <div>{s.message}</div>
        </div>
      ))}
    </div>
  );
}

export default function RunLog({ runId, onDone }: { runId: number; onDone?: (run: Run) => void }) {
  const [run, setRun] = useState<Run | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const last = useRef(-1);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    let alive = true;
    last.current = -1;
    setSteps([]);
    const tick = async () => {
      try {
        const r = await api.run(runId, last.current);
        if (!alive) return;
        setRun(r);
        if (r.steps && r.steps.length) {
          last.current = r.steps[r.steps.length - 1].idx;
          setSteps((prev) => [...prev, ...(r.steps || [])]);
        }
        if (r.status === "done" || r.status === "error") {
          doneRef.current?.(r);
          return;
        }
      } catch {
        /* повторим */
      }
      if (alive) setTimeout(tick, 700);
    };
    tick();
    return () => { alive = false; };
  }, [runId]);

  return (
    <div>
      <div className="row between mb">
        <div className="row">
          {run && (run.status === "running" || run.status === "queued") && <Spinner />}
          <b>Запуск #{runId}</b>
          {run && <span className={`badge ${run.status === "done" ? "ok" : run.status === "error" ? "conflict" : "blue"}`}>
            {run.status === "done" ? "готово" : run.status === "error" ? "ошибка" : run.status === "queued" ? "в очереди" : "работает"}
          </span>}
        </div>
        {run && <span className="tiny muted">{fmtDate(run.created_at, true)}</span>}
      </div>
      {run?.summary && <div className="notice small mb">Итог: {run.summary}</div>}
      {run?.error && <div className="error small mb">{run.error}</div>}
      <Steps steps={steps} />
    </div>
  );
}

export function RunDrawer({ runId, onClose, onDone, title }: { runId: number; onClose: () => void; onDone?: (r: Run) => void; title?: string }) {
  return (
    <div className="drawer-bg" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="row between mb">
          <h2>{title || "Работа агентов"}</h2>
          <button className="btn ghost" onClick={onClose}>Закрыть</button>
        </div>
        <RunLog runId={runId} onDone={onDone} />
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { RunDrawer } from "../RunLog";
import { ErrorBox, Icon, pct, Spinner, useAsync, useToast } from "../ui";

const EXAMPLES = [
  "Есть ли круглосуточная аптека в 10 минутах пешком",
  "Банкомат в пределах 300 м",
  "Детская площадка в 5 минутах ходьбы",
  "Есть ли в номере чайник и посуда",
  "Можно ли поздно заселиться после полуночи",
  "Парк или сквер в пределах 400 м",
];

function CompiledSpec({ spec }: { spec: any }) {
  return (
    <div className="card flat mt" style={{ background: "#fafbfd" }}>
      <div className="row between"><h3>{spec.title}</h3><span className="mono tiny muted">{spec.key}</span></div>
      <p className="small muted">{spec.description}</p>
      <dl className="kv">
        <dt>Рецепт агента</dt>
        <dd>{spec.recipe.map((s: any, i: number) => <div key={i} className="mono tiny">{s.tool} {s.params ? JSON.stringify(s.params) : ""}{s.when ? ` (когда: ${s.when})` : ""}</div>)}</dd>
        <dt>Поля значения</dt>
        <dd className="small">{Object.entries(spec.value_fields || {}).map(([k, v]: [string, any]) => `${k} (${v.type}) — ${v.title}`).join("; ")}</dd>
        {spec.question && <><dt>Вопрос отельеру</dt><dd className="small">{spec.question.text}</dd></>}
        <dt>Фразы для поиска</dt>
        <dd className="small">{(spec.search?.phrases || []).join(", ") || "—"}</dd>
        <dt>Срок годности</dt>
        <dd className="small">{spec.ttl_days} дней</dd>
      </dl>
    </div>
  );
}

export default function CharacteristicsPage() {
  const [params] = useSearchParams();
  const data = useAsync<{ items: any[]; groups: any[] }>(() => api.characteristics(), []);
  const [text, setText] = useState(params.get("add") || "");
  const [compiled, setCompiled] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [drawer, setDrawer] = useState<{ id: number; title: string } | null>(null);
  const toast = useToast();

  useEffect(() => { if (params.get("add")) document.getElementById("add-char")?.scrollIntoView({ behavior: "smooth" }); }, [params]);

  const compile = async () => {
    setBusy(true);
    setError(null);
    try { setCompiled(await api.compile(text)); } catch (e) { setError(e); }
    setBusy(false);
  };
  const create = async () => {
    setBusy(true);
    try {
      const r = await api.createChar(text, compiled?.spec);
      setDrawer({ id: r.run.id, title: `Агенты собирают «${r.spec.title}» для всех отелей` });
      setCompiled(null);
      setText("");
      data.reload();
    } catch (e) { setError(e); }
    setBusy(false);
  };

  const groups = data.data?.groups || [];
  return (
    <div className="page wide">
      <h1>Характеристики</h1>
      <p className="muted">Каждая характеристика — декларативный рецепт: какие инструменты агент пробует, когда спрашивает отельера, как сверяет источники и сколько хранится значение. Новую можно добавить текстом — без изменения кода.</p>

      <div className="card mb" id="add-char">
        <h2><Icon name="bolt" /> Добавить характеристику описанием</h2>
        <p className="small muted">Агент-компилятор подберёт инструменты из каталога (поиск объектов OSM, пешие маршруты с рельефом, видимость точки, климат) или, если в открытых данных этого нет, сделает вопрос отельеру.</p>
        <div className="row">
          <input className="input" value={text} onChange={(e) => { setText(e.target.value); setCompiled(null); }} placeholder="Например: есть ли круглосуточная аптека в 10 минутах пешком" />
          <button className="btn primary" disabled={!text.trim() || busy} onClick={compile}>{busy ? <Spinner /> : null} Скомпилировать</button>
        </div>
        <div className="row wrap mt-s">{EXAMPLES.map((e) => <button key={e} className="chip" onClick={() => { setText(e); setCompiled(null); }}>{e}</button>)}</div>
        <ErrorBox error={error} />
        {compiled && (
          <>
            <div className="row mt">
              <span className={`chip ${compiled.method === "llm" ? "violet" : "ghost"}`}>{compiled.method === "llm" ? "скомпилировано LLM" : "скомпилировано правилами"}</span>
              {compiled.errors?.length > 0 && <span className="chip bad">{compiled.errors.join("; ")}</span>}
            </div>
            <CompiledSpec spec={compiled.spec} />
            <button className="btn primary mt" disabled={busy || compiled.errors?.length > 0} onClick={create}>Создать и запустить агентов по всем отелям</button>
          </>
        )}
      </div>

      <ErrorBox error={data.error} />
      {data.loading && <Spinner />}
      {groups.map((g: any) => {
        const items = (data.data?.items || []).filter((x) => x.group === g.key);
        if (!items.length) return null;
        return (
          <div key={g.key} className="card">
            <h2>{g.title}</h2>
            <table className="t">
              <thead><tr><th style={{ width: "28%" }}>Характеристика</th><th>Рецепт агентов</th><th>Годность</th><th>Покрытие</th><th>Уверенность</th><th /></tr></thead>
              <tbody>
                {items.map((c: any) => (
                  <tr key={c.key} style={{ opacity: c.enabled ? 1 : 0.5 }}>
                    <td>
                      <b>{c.title}</b> {c.origin === "custom" && <span className="badge blue">добавлена{c.compiled_by ? ` · ${c.compiled_by}` : ""}</span>}
                      <div className="tiny muted">{c.description}</div>
                      {c.created_from_text && <div className="tiny muted">из текста: «{c.created_from_text}»</div>}
                    </td>
                    <td className="small">
                      {c.recipe.map((s: any, i: number) => (
                        <div key={i}><span className="mono tiny">{s.tool}</span> <span className="tiny muted">{s.agent}{s.when ? ` · ${s.when}` : ""}</span></div>
                      ))}
                      {c.question && <div className="tiny muted mt-s">Вопрос: {c.question}</div>}
                    </td>
                    <td className="small nowrap">{c.ttl_days} дн.</td>
                    <td style={{ minWidth: 110 }}><div className="meter"><i style={{ width: `${c.coverage * 100}%` }} /></div><span className="tiny muted">{pct(c.coverage)}</span></td>
                    <td className="small">{c.avg_confidence ? pct(c.avg_confidence) : "—"}</td>
                    <td className="nowrap">
                      <button className="btn small" title="Пересобрать для всех отелей" onClick={async () => { const r = await api.collectChar(c.key); setDrawer({ id: r.id, title: `Пересбор «${c.title}»` }); }}><Icon name="refresh" size={14} /></button>{" "}
                      <button className="btn small" onClick={async () => { const r = await api.toggleChar(c.key); toast.show(r.enabled ? "Включена" : "Выключена"); data.reload(); }}>{c.enabled ? "Выкл" : "Вкл"}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
      {drawer && <RunDrawer runId={drawer.id} title={drawer.title} onClose={() => { setDrawer(null); data.reload(); }} onDone={() => data.reload()} />}
      {toast.node}
    </div>
  );
}

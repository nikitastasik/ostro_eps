import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, Conflict, HotelListItem, Question, Run } from "../api";
import MapView from "../MapView";
import RunLog, { RunDrawer } from "../RunLog";
import { ErrorBox, fmtDate, Icon, SOURCE_RU, Spinner, useAsync, useToast } from "../ui";

const KIND_RU: Record<string, string> = {
  collect: "сбор по отелю", collect_all: "сбор по всем отелям", answer: "разбор ответа", actualize: "актуализация", scenario: "изменение среды", dedup: "склейка дублей",
};

function useInterval(fn: () => void, ms: number) {
  useEffect(() => {
    const t = setInterval(fn, ms);
    return () => clearInterval(t);
  }, [fn, ms]);
}

export default function AgentsPage() {
  const ov = useAsync<any>(() => api.overview(), []);
  const runs = useAsync<{ items: Run[] }>(() => api.runs("?limit=25"), []);
  const changes = useAsync<{ items: any[] }>(() => api.changes(), []);
  const conflicts = useAsync<{ items: Conflict[] }>(() => api.conflicts("?status=open&limit=30"), []);
  const questions = useAsync<{ items: Question[] }>(() => api.questions("?status=open&limit=12&order=utility"), []);
  const hotels = useAsync<{ items: HotelListItem[] }>(() => api.hotels(), []);
  const [selRun, setSelRun] = useState<number | null>(null);
  const [drawer, setDrawer] = useState<{ id: number; title: string } | null>(null);
  const [scHotel, setScHotel] = useState("");
  const [scHeight, setScHeight] = useState(50);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const refresh = () => { ov.reload(); runs.reload(); changes.reload(); conflicts.reload(); questions.reload(); };
  useInterval(() => { runs.reload(); ov.reload(); }, 5000);

  const seaHotels = useMemo(() => (hotels.data?.items || []).filter((h) => h.badges.sea_view), [hotels.data]);
  useEffect(() => { if (!scHotel && seaHotels.length) setScHotel(seaHotels[0].id); }, [seaHotels, scHotel]);

  const placeBuilding = async () => {
    if (!scHotel) return;
    setBusy(true);
    try {
      const g = await api.geo(scHotel);
      const card = await api.hotel(scHotel);
      const sea = card.groups.flatMap((x) => x.items).find((x) => x.key === "sea_view");
      const best = (sea?.value as any)?.best_facade;
      const f = g.facades.find((x) => x.key === best) || g.facades[0];
      const dist = 55;
      const az = (f.normal_az * Math.PI) / 180;
      const lat = f.lat + (dist * Math.cos(az)) / 111132;
      const lon = f.lon + (dist * Math.sin(az)) / (111320 * Math.cos((f.lat * Math.PI) / 180));
      const r = await api.scenarioBuilding({ lat, lon, height_m: scHeight, width_m: 60, length_m: 24, angle_deg: -f.normal_az,
        label: `Новый ЖК ${Math.round(scHeight / 3)} эт. перед „${card.name}“` });
      setDrawer({ id: r.run.id, title: "Агенты пересчитывают виды после стройки" });
      ov.reload();
    } catch (e) { toast.show(String(e)); }
    setBusy(false);
  };

  const o = ov.data;
  return (
    <div className="page wide">
      <h1>Агенты</h1>
      <p className="muted">Кто что делает, очередь запусков, вопросы и противоречия, актуализация данных и реакция на изменения городской среды.</p>
      <ErrorBox error={ov.error} />
      {o && (
        <div className="grid four">
          <div className="card kpi"><div className="v">{(o.runs.done || 0) + (o.runs.error || 0)}</div><div className="l">запусков выполнено{o.runs.running ? ` · ${o.runs.running} в работе` : ""}{o.runs.queued ? ` · ${o.runs.queued} в очереди` : ""}</div></div>
          <div className="card kpi"><div className="v">{o.questions.open || 0}</div><div className="l">вопросов ждут отельеров · отвечено {o.questions.answered || 0}</div></div>
          <div className="card kpi"><div className="v">{o.conflicts.open || 0}</div><div className="l">противоречий открыто · разобрано {o.conflicts.resolved || 0}</div></div>
          <div className="card kpi"><div className="v">{o.stale}</div><div className="l">значений устарело · {o.stale_soon} истекают в 30 дней</div></div>
        </div>
      )}

      {o && (
        <div className="grid three mt">
          {o.agents.map((a: any) => (
            <div key={a.name} className="card agent-card">
              <h3><Icon name="agents" /> {a.name}</h3>
              {a.role && <p className="small muted">{a.role}</p>}
              {a.tools.map((t: any) => (
                <div key={t.name} className="small mt-s"><span className="mono tiny">{t.name}</span> — {t.title}<div className="tiny muted">{t.sources.join(" · ")}</div></div>
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="grid two mt">
        <div className="card">
          <div className="row between"><h2>Запуски</h2><button className="btn small" onClick={refresh}><Icon name="refresh" size={14} /> Обновить</button></div>
          {runs.data?.items.map((r) => (
            <div key={r.id} className="row between" style={{ padding: "7px 0", borderBottom: "1px solid #edf0f5", cursor: "pointer" }} onClick={() => setSelRun(r.id)}>
              <div className="small">
                <b>#{r.id}</b> {KIND_RU[r.kind] || r.kind}{r.hotel_name ? ` · ${r.hotel_name}` : ""} <span className="tiny muted">· {r.trigger} · {fmtDate(r.created_at, true)}</span>
                <div className="tiny muted">{r.summary || r.error || ""}</div>
              </div>
              <span className={`badge ${r.status === "done" ? "ok" : r.status === "error" ? "conflict" : "blue"}`}>{r.status}</span>
            </div>
          ))}
        </div>
        <div className="card">
          {selRun ? <RunLog key={selRun} runId={selRun} /> : <div className="empty small">Выберите запуск слева, чтобы увидеть шаги: какие инструменты выбирал агент и почему, что вернули источники, где спросил отельера</div>}
        </div>
      </div>

      <div className="grid two mt">
        <div className="card">
          <h2>Актуализация</h2>
          <p className="small muted">
            У каждого значения есть срок годности: стройки — 30 дней, POI — 90, шум — 120, виды — год, климат — 10 лет.
            Арбитр находит устаревшее, оркестратор перепроверяет его теми же инструментами, а изменения попадают в ленту ниже.
            Фоновая проверка идёт каждые {o?.actualize_interval_min ?? "…"} мин.
          </p>
          <div className="row wrap">
            <button className="btn" disabled={busy} onClick={async () => { const r = await api.age(200); toast.show(`Состарили ${r.facts_aged} фактов на 200 дней — устарело ${r.became_stale} значений`); refresh(); }}>
              Демо: «прошло 200 дней»
            </button>
            <button className="btn primary" disabled={busy} onClick={async () => { const r = await api.actualize(); setDrawer({ id: r.id, title: "Актуализация устаревших данных" }); }}>
              <Icon name="refresh" /> Перепроверить устаревшее
            </button>
          </div>
          <h3 className="mt">Лента изменений</h3>
          {changes.data?.items.length === 0 && <div className="small muted">Изменений пока не было</div>}
          <div style={{ maxHeight: 360, overflow: "auto" }}>
            {changes.data?.items.map((c) => (
              <div key={c.id} className="small" style={{ padding: "8px 0", borderBottom: "1px solid #edf0f5" }}>
                <div className="tiny muted">{fmtDate(c.at, true)} · {c.reason} · <Link to={`/hotel/${encodeURIComponent(c.hotel_id)}`}>{c.hotel_name}</Link> · {c.title}</div>
                <div><s className="muted">{c.old}</s></div>
                <div>{c.new}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <h2>Сценарий: рядом построили дом</h2>
          <p className="small muted">Ставим новое здание перед «морскими» окнами выбранного отеля. Агенты пересчитывают виды, закат и шум для всех отелей в радиусе 1,2 км, а изменения попадают в ленту.</p>
          <div className="row wrap">
            <select className="select" value={scHotel} onChange={(e) => setScHotel(e.target.value)} style={{ maxWidth: 320 }}>
              {seaHotels.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
            <select className="select" value={scHeight} onChange={(e) => setScHeight(Number(e.target.value))} style={{ width: 150 }}>
              {[20, 35, 50, 70].map((x) => <option key={x} value={x}>{x} м (~{Math.round(x / 3)} эт.)</option>)}
            </select>
            <button className="btn primary" disabled={busy || !scHotel} onClick={placeBuilding}>{busy ? <Spinner /> : <Icon name="crane" />} Построить</button>
            <button className="btn" onClick={async () => { const r = await api.scenarioReset(); if (r.run) setDrawer({ id: r.run.id, title: "Откат сценариев: пересчёт видов" }); refresh(); }}>Снести всё построенное</button>
          </div>
          {o?.overrides?.length > 0 && (
            <div className="mt">
              <MapView className="map small" fitKey={String(o.overrides.length)}
                polygons={o.overrides.filter((x: any) => x.active).map((x: any) => ({ points: x.geometry.coordinates[0], color: "#e0464b", title: `${x.label} (${x.height_m} м)` }))}
                markers={(hotels.data?.items || []).filter((h) => h.id === scHotel).map((h) => ({ id: h.id, lat: h.lat, lon: h.lon, color: "#0a6cff", title: h.name }))} />
              {o.overrides.map((x: any) => <div key={x.id} className="tiny muted mt-s">{x.active ? "●" : "○"} {x.label} — {x.height_m} м · {fmtDate(x.created_at, true)}</div>)}
            </div>
          )}
        </div>
      </div>

      <div className="grid two mt">
        <div className="card">
          <h2>Противоречия на модерации</h2>
          <p className="small muted">Арбитр не выбирает молча: если расчёт и отельер расходятся, он снижает уверенность, задаёт уточняющий вопрос и показывает обе версии.</p>
          {conflicts.data?.items.length === 0 && <div className="small muted">Открытых противоречий нет</div>}
          {conflicts.data?.items.map((c) => (
            <div key={c.id} className="qcard">
              <div className="row between"><b className="small"><Link to={`/hotel/${encodeURIComponent(c.hotel_id)}`}>{c.hotel_name}</Link> · {c.title}</b><span className="tiny muted">{fmtDate(c.created_at, true)}</span></div>
              <div className="small mt-s">{c.description}</div>
              {c.facts.map((f) => (
                <div key={f.id} className="row between mt-s" style={{ alignItems: "flex-start" }}>
                  <div className="small"><span className={`src ${f.source}`}>{f.source_ru}</span> {f.summary}</div>
                  <button className="btn small" onClick={async () => { await api.resolveConflict(c.id, f.id); toast.show(`Принято: ${SOURCE_RU[f.source] || f.source}`); refresh(); }}>Принять</button>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Вопросы, ждущие отельеров</h2>
          {questions.data?.items.map((q) => (
            <div key={q.id} className="qcard open">
              <div className="tiny muted"><Link to={`/hotelier?hotel=${encodeURIComponent(q.hotel_id)}`}>{q.hotel_name}</Link> · {(q.titles || []).join(", ")}</div>
              <div className="small mt-s">{q.text}</div>
              {q.utility && <div className="tiny utility mt-s">{q.utility.score > 0 ? "↑ " : ""}{q.utility.text}</div>}
              <button className="btn small mt-s" onClick={async () => { const r = await api.simulate(q.id); toast.show(`Отельер ответил: ${r.question.answer_text}`); refresh(); }}>Ответить за отельера (симуляция)</button>
            </div>
          ))}
        </div>
      </div>
      {drawer && <RunDrawer runId={drawer.id} title={drawer.title} onClose={() => { setDrawer(null); refresh(); }} onDone={() => refresh()} />}
      {toast.node}
    </div>
  );
}

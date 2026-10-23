import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, CharValue, Fact, GeoData, HotelCard, Run } from "../api";
import MapView from "../MapView";
import Panorama from "../Panorama";
import RunLog, { RunDrawer } from "../RunLog";
import { Confidence, ErrorBox, fmtDate, Icon, KIND_RU, plural, Sources, Spinner, StatusBadge, useAsync, useCompare, useToast } from "../ui";

function noiseColor(db?: number) {
  if (db == null) return "#9aa6b8";
  if (db < 40) return "#14a05a";
  if (db < 45) return "#6aa72f";
  if (db < 55) return "#e3b100";
  if (db < 60) return "#ee7d22";
  return "#e0464b";
}

const EVIDENCE_RU: Record<string, string> = {
  method: "метод", note: "примечание", model: "модель", algorithm: "алгоритм", rule: "правило", confidence_factors: "из чего уверенность",
  floors_checked: "проверено этажей", start_offset_m: "до ближайшего узла графа, м", answer: "ответ отельера", question: "вопрос",
  parse_method: "как разобран ответ", missing: "не удалось извлечь", validation: "проверка метода", score_formula: "формула",
  hours_known: "с известными часами работы", places_checked: "проверено объектов", tags: "теги OSM", receiver_height_m: "высота приёмника, м",
};

function EvidenceView({ ev }: { ev: Record<string, any> }) {
  const keys = Object.keys(ev || {}).filter((k) => !["fact_id", "question_id", "sunset_azimuths", "drive_speeds", "landmarks_checked", "peaks_checked", "target", "confirmed_fact_id", "answered_by"].includes(k));
  if (!keys.length) return null;
  return (
    <dl className="kv mt-s">
      {keys.map((k) => {
        const v = ev[k];
        let txt: string;
        if (v && typeof v === "object" && !Array.isArray(v)) {
          txt = Object.entries(v).map(([a, b]) => `${a}: ${typeof b === "number" ? Math.round((b as number) * 100) / 100 : String(b)}`).join("; ");
        } else if (Array.isArray(v)) {
          txt = v.join(", ");
        } else {
          txt = String(v);
        }
        return [<dt key={k + "t"}>{EVIDENCE_RU[k] || k}</dt>, <dd key={k + "d"} className="small">{txt}</dd>];
      })}
    </dl>
  );
}

function CharItem({ c, hotelId, onRecalc }: { c: CharValue; hotelId: string; onRecalc: (keys: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const facts = useAsync<{ items: Fact[] }>(() => (open ? api.facts(hotelId, c.key) : Promise.resolve({ items: [] })), [open, hotelId, c.key, c.updated_at]);
  return (
    <div className="char">
      <div className="char-head" onClick={() => setOpen(!open)}>
        <div className="char-icon"><Icon name={c.icon} /></div>
        <div>
          <div className="row" style={{ gap: 8 }}>
            <span className="char-title">{c.title}</span>
            {c.origin === "custom" && <span className="badge blue">добавлена</span>}
          </div>
          <div className={`char-summary ${c.summary ? "" : "muted"}`}>
            {c.summary || (c.status === "asked" ? "Нет в открытых данных — агент спросил отельера, ждём ответа" : "Нет данных")}
          </div>
          <div className="char-meta">
            <StatusBadge status={c.status} />
            {c.status !== "unknown" && c.status !== "asked" && <Confidence value={c.confidence} />}
            <Sources sources={c.sources} />
            {c.valid_until && <span className="tiny muted">годно до {fmtDate(c.valid_until)}</span>}
          </div>
        </div>
        <span className="muted">{open ? "▴" : "▾"}</span>
      </div>
      {open && (
        <div className="char-body">
          <div className="small"><b>Как получено:</b> {c.explanation || "—"}</div>
          <div className="small muted mt-s">{c.description}</div>
          <div className="hr" />
          <div className="row between"><b className="small">Факты от источников</b>
            <button className="btn small" onClick={() => onRecalc([c.key])}><Icon name="refresh" size={14} /> Пересчитать</button></div>
          {facts.loading && <Spinner />}
          {facts.data?.items.map((f) => (
            <div className="fact" key={f.id}>
              <div className="row wrap" style={{ gap: 6 }}>
                <span className={`src ${f.source}`}>{f.source_ru}</span>
                <span className="tiny mono muted">{f.method}</span>
                {f.status !== "active" && <span className="badge unknown">{f.status === "superseded" ? "заменён новым" : f.status === "rejected" ? "отклонён" : f.status}</span>}
                <Confidence value={f.confidence} />
                <span className="tiny muted">получен {fmtDate(f.observed_at, true)} · годен до {fmtDate(f.valid_until)}{f.stale ? " · устарел" : ""}</span>
              </div>
              <div className="small mt-s">{f.summary}</div>
              <div className="tiny muted">{f.source_detail}</div>
              <EvidenceView ev={f.evidence} />
            </div>
          ))}
          {facts.data && facts.data.items.length === 0 && <div className="small muted">Фактов пока нет</div>}
        </div>
      )}
    </div>
  );
}

function Profile({ profile }: { profile: [number, number][] }) {
  if (!profile || profile.length < 2) return null;
  const W = 440, H = 90;
  const maxD = profile[profile.length - 1][0] || 1;
  const zs = profile.map((p) => p[1]);
  const zmin = Math.min(...zs), zmax = Math.max(...zs, zmin + 5);
  const pts = profile.map(([d, z]) => `${(d / maxD) * W},${H - ((z - zmin) / (zmax - zmin)) * (H - 14) - 4}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H + 16}`} style={{ width: "100%", height: "auto" }}>
      <polygon points={`0,${H} ${pts} ${W},${H}`} fill="#dbeafe" />
      <polyline points={pts} fill="none" stroke="#0a6cff" strokeWidth={2} />
      <text x={2} y={H + 13} fontSize={11} fill="#66758c">отель · {Math.round(profile[0][1])} м</text>
      <text x={W - 2} y={H + 13} fontSize={11} fill="#66758c" textAnchor="end">{Math.round(maxD)} м · {Math.round(profile[profile.length - 1][1])} м</text>
      <text x={W - 2} y={12} fontSize={11} fill="#66758c" textAnchor="end">перепад {Math.round(zmax - zmin)} м</text>
    </svg>
  );
}

export default function HotelPage() {
  const { id = "" } = useParams();
  const card = useAsync<HotelCard>(() => api.hotel(id), [id]);
  const geo = useAsync<GeoData>(() => api.geo(id), [id]);
  const [runId, setRunId] = useState<number | null>(null);
  const [openRun, setOpenRun] = useState<number | null>(null);
  const cmp = useCompare();
  const toast = useToast();

  const recalc = async (keys?: string[]) => {
    try {
      const r = await api.collect(id, keys);
      setRunId(r.id);
    } catch (e) {
      toast.show(String(e));
    }
  };
  const onDone = (r: Run) => {
    toast.show(`Агенты закончили: ${r.summary || r.status}`);
    card.reload();
    geo.reload();
  };

  if (card.error) return <div className="page"><ErrorBox error={card.error} /></div>;
  const h = card.data;
  if (!h) return <div className="page"><div className="empty"><Spinner /></div></div>;
  const g = geo.data;
  const sea = h.groups.flatMap((x) => x.items).find((x) => x.key === "sea_view");
  const bestFacade = (sea?.value as any)?.best_facade as string | undefined;
  const beach = g?.routes?.beach_access;
  const openQs = (h.questions || []).filter((q) => q.status === "open").length;
  const lines: { points: [number, number][]; color: string; weight?: number; dash?: string; title?: string; noFit?: boolean }[] = [];
  if (beach) lines.push({ points: beach.line, color: "#0a6cff", weight: 5, title: "Пешком до пляжа" });
  if (g?.routes?.center_access) lines.push({ points: g.routes.center_access.line, color: "#7c4ddb", weight: 3, dash: "6 6", title: "Пешком до центра", noFit: true });

  return (
    <div className="page wide">
      <div className="hotel-head">
        <div>
          <div className="small muted"><Link to="/hotels">Отели</Link> / {KIND_RU[h.kind] || h.kind}</div>
          <h1>{h.name}</h1>
          <div className="muted">
            {h.address || "адрес не указан"}{h.stars ? ` · ${"★".repeat(h.stars)}` : ""}
            {h.building.levels ? ` · здание ${h.building.levels} эт.` : ""}
            {h.website && <> · <a href={h.website} target="_blank" rel="noreferrer">сайт</a></>}
            {h.osm && <> · <a href={`https://www.openstreetmap.org/${h.osm.startsWith("n") ? "node" : h.osm.startsWith("w") ? "way" : "relation"}/${h.osm.slice(1)}`} target="_blank" rel="noreferrer">OSM</a></>}
          </div>
          <div className="row mt-s">
            <span className="chip accent">Знаем {h.known} из {h.total} характеристик</span>
            {openQs > 0 && <span className="chip violet">{openQs} {plural(openQs, "вопрос ждёт", "вопроса ждут", "вопросов ждут")} отельера</span>}
            {(h.conflicts || []).filter((c) => c.status === "open").length > 0 && <span className="chip bad">противоречий: {(h.conflicts || []).filter((c) => c.status === "open").length}</span>}
          </div>
        </div>
        <div className="row wrap" style={{ justifyContent: "flex-end" }}>
          <button className="btn primary" onClick={() => recalc()}><Icon name="refresh" /> Пересчитать агентами</button>
          <button className={`btn ${cmp.has(h.id) ? "primary" : ""}`} onClick={() => cmp.toggle(h.id, h.name)}>{cmp.has(h.id) ? "В сравнении" : "Добавить в сравнение"}</button>
          <Link className="btn" to={`/hotelier?hotel=${encodeURIComponent(h.id)}`}>Кабинет отельера</Link>
        </div>
      </div>

      <div className="layout-hotel">
        <div className="groups">
          <div className="card">
            <h2>Что видно из окна</h2>
            <p className="small muted">Профиль горизонта для выбранной стороны здания и этажа: рельеф, дома, деревья и море по лучам с шагом 1°. Линии — путь Солнца перед закатом 15-го числа каждого месяца; сплошная — закат виден из этого окна.</p>
            <Panorama hotelId={h.id} initialFacade={bestFacade} />
          </div>
          {h.groups.map((grp) => (
            <div className="card" key={grp.key}>
              <h2>{grp.title}</h2>
              {grp.items.map((c) => <CharItem key={c.key} c={c} hotelId={h.id} onRecalc={recalc} />)}
            </div>
          ))}
        </div>
        <div className="col" style={{ gap: 14 }}>
          <div className="card">
            <h3>Здание, стороны и путь до моря</h3>
            {g ? (
              <MapView
                className="map small"
                fitKey={h.id}
                polygons={g.footprint ? [{ points: g.footprint, color: "#13213b", fill: "#0a6cff", title: "Контур здания (OSM)" }] : []}
                arrows={g.facades.map((f) => ({ lat: f.lat, lon: f.lon, az: f.normal_az, color: noiseColor(f.night_db), label: f.key,
                  title: [f.key + "-сторона", f.night_db != null ? `ночью ≈${Math.round(f.night_db)} дБ` : "", f.sea_view, f.sunset_view].filter(Boolean).join(" · ") }))}
                lines={lines}
                markers={g.footprint ? [] : [{ id: h.id, lat: h.lat, lon: h.lon, color: "#13213b", label: "", title: h.name }]}
              />
            ) : <Spinner />}
            <div className="legend">
              <span><i style={{ background: "#14a05a" }} />тихо ночью</span><span><i style={{ background: "#e3b100" }} />умеренно</span>
              <span><i style={{ background: "#e0464b" }} />шумно</span><span><i style={{ background: "#0a6cff" }} />путь до пляжа</span>
              {g?.routes?.center_access && <span><i style={{ background: "#7c4ddb" }} />пунктир — путь до центра</span>}
            </div>
            {beach && <div className="mt-s"><div className="small"><b>Профиль пути до пляжа:</b> {beach.summary}</div><Profile profile={beach.profile} /></div>}
          </div>
          {g && g.facades.length > 0 && (
            <div className="card">
              <h3>По сторонам здания</h3>
              <table className="t facade-table">
                <thead><tr><th>Сторона</th><th>Ночью</th><th>Море</th><th>Закат</th></tr></thead>
                <tbody>
                  {g.facades.map((f) => (
                    <tr key={f.key}>
                      <td className="nowrap"><b>{f.key}</b> <span className="tiny muted">{Math.round(f.normal_az)}°</span></td>
                      <td className="nowrap"><span className="dot" style={{ background: noiseColor(f.night_db) }} /> {f.night_db != null ? `${Math.round(f.night_db)} дБ` : "—"}</td>
                      <td className="small">{f.sea_view || "—"}</td>
                      <td className="small">{f.sunset_view || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="grid two mt">
        <div className="card">
          <h2>Вопросы отельеру</h2>
          {(h.questions || []).length === 0 && <div className="small muted">Вопросов нет</div>}
          {(h.questions || []).slice(0, 12).map((q) => (
            <div key={q.id} className={`qcard ${q.status === "open" ? "open" : ""}`}>
              <div className="row between"><span className={`badge ${q.status === "open" ? "asked" : "ok"}`}>{q.status === "open" ? "ждёт ответа" : q.status === "answered" ? "отвечен" : "закрыт"}</span><span className="tiny muted">{fmtDate(q.created_at, true)}</span></div>
              <div className="small mt-s">{q.text}</div>
              {q.answer_text && <div className="small mt-s"><b>Ответ{q.answered_by === "simulator" ? " (симуляция)" : ""}:</b> {q.answer_text}</div>}
              {q.parsed && Object.entries(q.parsed).map(([k, v]: [string, any]) => (
                <div key={k} className="tiny muted mt-s">Агент разложил ({v.method === "llm" ? "LLM" : "правила"}): <span className="mono">{JSON.stringify(v.fields)}</span></div>
              ))}
            </div>
          ))}
          <Link className="btn small mt" to={`/hotelier?hotel=${encodeURIComponent(h.id)}`}>Ответить в кабинете отельера →</Link>
        </div>
        <div className="card">
          <h2>Противоречия источников</h2>
          {(h.conflicts || []).length === 0 && <div className="small muted">Источники согласованы</div>}
          {(h.conflicts || []).map((c) => (
            <div key={c.id} className="qcard">
              <div className="row between"><b className="small">{c.title}</b><span className={`badge ${c.status === "open" ? "conflict" : "ok"}`}>{c.status === "open" ? "открыто" : "разобрано"}</span></div>
              <div className="small mt-s">{c.description}</div>
              {c.resolution && <div className="tiny muted mt-s">Решение: {c.resolution}</div>}
            </div>
          ))}
          <h2 className="mt">История изменений</h2>
          {(h.changes || []).length === 0 && <div className="small muted">Изменений пока не было</div>}
          {(h.changes || []).map((c, i) => (
            <div key={i} className="small mt-s"><span className="tiny muted">{fmtDate(c.at, true)} · {c.reason}</span><br /><s className="muted">{c.old}</s><br />{c.new}</div>
          ))}
        </div>
      </div>

      <div className="card mt">
        <h2>Журнал агентов по отелю</h2>
        <div className="grid two">
          <div>
            {(h.runs || []).map((r) => (
              <div key={r.id} className="row between" style={{ padding: "7px 0", borderBottom: "1px solid #edf0f5" }}>
                <span className="small"><b>#{r.id}</b> {r.kind} · {r.trigger} · <span className="muted">{fmtDate(r.created_at, true)}</span><br /><span className="tiny muted">{r.summary}</span></span>
                <button className="btn small" onClick={() => setOpenRun(r.id)}>Шаги</button>
              </div>
            ))}
          </div>
          <div>{openRun ? <RunLog runId={openRun} /> : <div className="empty small">Выберите запуск, чтобы увидеть, какие инструменты агенты вызывали и почему</div>}</div>
        </div>
      </div>
      {runId && <RunDrawer runId={runId} onClose={() => setRunId(null)} onDone={onDone} title={`Агенты собирают данные: ${h.name}`} />}
      {toast.node}
    </div>
  );
}

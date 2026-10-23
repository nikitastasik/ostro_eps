import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, Criterion, Meta, SearchResponse, SearchResult } from "../api";
import MapView from "../MapView";
import { Confidence, ErrorBox, Icon, KIND_RU, ScoreRing, scoreColor, Sources, Spinner, useCompare } from "../ui";

const OP_RU: Record<string, string> = { le: "≤", ge: "≥", lt: "<", gt: ">", eq: "=", ne: "≠", in: "∈", contains: "∋", nonempty: "" };
const MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"];

const FIELD_UNIT: Record<string, string> = {
  night_db: " дБ ночью", minutes_to: " мин пешком", walk_m: " м пешком", climb_back_m: " м подъёма", park_m: " м до парка",
  station_minutes: " мин до станции", levels: " эт.", max_weight_kg: " кг", wifi_mbps: " Мбит/с", min_floor: "-го этажа",
};

function critText(c: Criterion & { field_title?: string }) {
  const month = c.month ? ` в ${MONTHS_PREP[c.month - 1]}` : "";
  const simple = c.op === "eq" || c.op === "nonempty" || c.op === "in" || c.op === "contains" || (c.op === "ge" && c.value === 1);
  if (simple) return `${c.label}${month}`;
  const unit = FIELD_UNIT[c.field] ?? (c.field_title ? ` (${c.field_title.toLowerCase()})` : "");
  return `${c.label}: ${OP_RU[c.op] || c.op} ${Math.round(Number(c.value) * 10) / 10}${unit}${month}`;
}

function ResultCard({ r, rank, hl, onHover }: { r: SearchResult; rank: number; hl: boolean; onHover: (id: string | null) => void }) {
  const cmp = useCompare();
  const h = r.hotel;
  return (
    <div className={`card result ${hl ? "hl" : ""}`} onMouseEnter={() => onHover(h.id)} onMouseLeave={() => onHover(null)} id={`r-${h.id}`}>
      <div className="col" style={{ alignItems: "center" }}>
        <ScoreRing score={r.score} />
        <span className="tiny muted">#{rank}</span>
      </div>
      <div>
        <div className="row between" style={{ alignItems: "flex-start" }}>
          <div>
            <h3 style={{ marginBottom: 2 }}><Link to={`/hotel/${encodeURIComponent(h.id)}`}>{h.name}</Link></h3>
            <div className="small muted">{KIND_RU[h.kind] || h.kind}{h.stars ? ` · ${"★".repeat(h.stars)}` : ""}{h.address ? ` · ${h.address}` : ""}{h.levels ? ` · ${h.levels} эт.` : ""}</div>
          </div>
          <div className="row">
            <button className={`btn small ${cmp.has(h.id) ? "primary" : ""}`} onClick={() => cmp.toggle(h.id, h.name)}>
              {cmp.has(h.id) ? "В сравнении" : "Сравнить"}
            </button>
            <Link className="btn small" to={`/hotel/${encodeURIComponent(h.id)}`}>Подробнее</Link>
          </div>
        </div>
        {r.room && <div className="room"><Icon name="compass" size={15} /> <b>Совет агента:</b> {r.room.text}</div>}
        <div className="needs">
          {r.needs.map((n) => (
            <div className="need" key={n.key}>
              <span className={`ic ${n.status}`}>{n.status === "match" ? "✓" : n.status === "partial" ? "~" : n.status === "miss" ? "✕" : "?"}</span>
              <div><b>{n.title}:</b> <span className={n.status === "unknown" ? "muted" : ""}>{n.text || "—"}</span></div>
              <div className="row" style={{ gap: 6 }}>
                <Sources sources={n.sources} />
                {n.status !== "unknown" && <Confidence value={n.confidence} label={false} />}
              </div>
            </div>
          ))}
        </div>
        {r.excluded && <div className="small" style={{ color: "var(--bad)", marginTop: 6 }}>Не выполнено обязательное условие</div>}
      </div>
    </div>
  );
}

export default function SearchPage({ meta }: { meta: Meta | null }) {
  const [params, setParams] = useSearchParams();
  const [text, setText] = useState(params.get("q") || "");
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [hl, setHl] = useState<string | null>(null);
  const nav = useNavigate();

  const run = async (q: string, criteria?: Criterion[]) => {
    if (!q.trim() && !criteria) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.search(q, criteria);
      setRes(r);
      setParams(q ? { q } : {}, { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const q = params.get("q");
    if (q) run(q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const removeCriterion = (i: number) => {
    if (!res) return;
    const crit = res.query.criteria.filter((_, j) => j !== i);
    if (crit.length === 0) { setRes(null); return; }
    run(text, crit);
  };

  const results = res?.results.filter((r) => !r.excluded) || [];
  return (
    <div className="page wide">
      <div className="hero">
        <h1>Найдите отель, который подходит именно вам</h1>
        <p>
          Опишите своими словами, что для вас важно. Агенты знают об отелях Сочи то, чего нет в карточках:
          что видно из окна на каждом этаже, когда виден закат, насколько шумно ночью с каждой стороны здания,
          сколько на самом деле идти до пляжа в горку — и что ответили отельеры.
        </p>
        <form className="searchbox" onSubmit={(e) => { e.preventDefault(); run(text); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="например: тихий номер с видом на закат недалеко от моря" autoFocus />
          <button className="btn primary" type="submit" disabled={loading}>{loading ? <Spinner /> : <Icon name="search" />} Найти</button>
        </form>
        <div className="examples">
          {(meta?.examples || []).map((ex) => (
            <button key={ex} onClick={() => { setText(ex); run(ex); }}>{ex}</button>
          ))}
        </div>
      </div>

      <ErrorBox error={error} />

      {res && (
        <div className="card mt">
          <div className="row between wrap">
            <div className="understood">
              <b className="small">Как агент понял запрос</b>
              <span className={`chip ${res.query.parser === "llm" ? "violet" : "ghost"}`}>{res.query.parser === "llm" ? "LLM" : res.query.parser === "manual" ? "вручную" : "правила"}</span>
              {res.query.criteria.map((c, i) => (
                <span key={i} className={`chip ${c.required ? "warn" : "accent"}`} title={c.source_text ? `из фразы «${c.source_text}»` : undefined}>
                  {critText(c)}{c.weight > 1 ? " · важно" : c.weight < 1 ? " · желательно" : ""}
                  <span className="x" onClick={() => removeCriterion(i)}>✕</span>
                </span>
              ))}
              {res.query.criteria.length === 0 && <span className="muted small">не нашёл знакомых характеристик</span>}
            </div>
            <span className="small muted">{results.length} подходящих из {res.total}</span>
          </div>
          {res.query.unmatched.length > 0 && (
            <div className="notice warn small mt-s">
              Не понял: {res.query.unmatched.map((u) => `«${u}»`).join(", ")}. Такой характеристики пока нет —{" "}
              <a href="#" onClick={(e) => { e.preventDefault(); nav(`/characteristics?add=${encodeURIComponent(res.query.unmatched[0])}`); }}>
                добавить её описанием
              </a>, и агенты соберут данные для всех отелей.
            </div>
          )}
        </div>
      )}

      {res && (
        <div className="layout-search">
          <div>
            {results.slice(0, 20).map((r, i) => (
              <ResultCard key={r.hotel.id} r={r} rank={i + 1} hl={hl === r.hotel.id} onHover={setHl} />
            ))}
            {results.length === 0 && <div className="card empty">Ничего не нашлось — попробуйте убрать одно из условий.</div>}
          </div>
          <div className="sticky">
            <MapView
              fitKey={res.query.text + res.query.criteria.length}
              markers={results.slice(0, 20).map((r, i) => ({
                id: r.hotel.id, lat: r.hotel.lat, lon: r.hotel.lon, label: String(i + 1), color: scoreColor(r.score),
                title: `${r.hotel.name} — ${r.score}`, highlight: hl === r.hotel.id,
              }))}
              onMarkerClick={(id) => { setHl(id); document.getElementById(`r-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); }}
            />
            <p className="tiny muted mt-s">Оценка учитывает не только совпадение, но и уверенность данных: непроверенное значение весит меньше.</p>
          </div>
        </div>
      )}

      {!res && !loading && (
        <div className="grid three mt">
          <div className="card"><h3><Icon name="sunset" /> Вид и закат по этажам</h3><p className="small muted">Лучи из каждого окна по рельефу Copernicus DEM и высотам 44 тыс. зданий OSM; положение Солнца по месяцам.</p></div>
          <div className="card"><h3><Icon name="moon" /> Тишина с каждой стороны</h3><p className="small muted">Шум дорог, железной дороги и ночных баров с экранированием домами — отдельно для каждого фасада.</p></div>
          <div className="card"><h3><Icon name="chat" /> Агенты спрашивают отельеров</h3><p className="small muted">Чего нет в открытых данных — питомцы, шторы, окна — агент спрашивает и раскладывает свободный ответ по полям.</p></div>
        </div>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, Criterion, Meta, SearchResponse, SearchResult } from "../api";
import MapView from "../MapView";
import { WishButton } from "../Wish";
import { ErrorBox, Icon, KIND_RU, NeedsList, plural, ScoreRing, scoreColor, Spinner, useCompare } from "../ui";

const OP_RU: Record<string, string> = { le: "≤", ge: "≥", lt: "<", gt: ">", eq: "=", ne: "≠", in: "∈", contains: "∋", nonempty: "" };
const MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"];

const FIELD_UNIT: Record<string, string> = {
  night_db: " дБ", minutes_to: " мин пешком", walk_m: " м пешком", climb_back_m: " м подъёма", park_m: " м до парка",
  station_minutes: " мин до станции", levels: " эт.", max_weight_kg: " кг", wifi_mbps: " Мбит/с", min_floor: "-го этажа",
};

type Crit = Criterion & { field_title?: string };

function critDetail(c: Crit) {
  const simple = c.op === "eq" || c.op === "nonempty" || c.op === "in" || c.op === "contains" || (c.op === "ge" && c.value === 1);
  if (simple) return "";
  const unit = FIELD_UNIT[c.field] ?? (c.field_title ? ` (${c.field_title.toLowerCase()})` : "");
  return `${OP_RU[c.op] || c.op} ${Math.round(Number(c.value) * 10) / 10}${unit}`;
}

/** Один чип на пожелание: критерии одной характеристики с одной подписью («Пляж без подъёмов и лестниц») — вместе. */
function groupCriteria(criteria: Crit[]) {
  const groups: { idx: number[]; crits: Crit[] }[] = [];
  criteria.forEach((c, i) => {
    const g = groups.find((x) => x.crits[0].key === c.key && x.crits[0].label === c.label && x.crits[0].month === c.month);
    if (g) { g.idx.push(i); g.crits.push(c); } else groups.push({ idx: [i], crits: [c] });
  });
  return groups;
}

function critText(crits: Crit[]) {
  const c = crits[0];
  const month = c.month ? ` в ${MONTHS_PREP[c.month - 1]}` : "";
  const details = crits.map(critDetail).filter(Boolean);
  return `${c.label}${details.length ? `: ${details.join(", ")}` : ""}${month}`;
}

function ResultCard({ r, rank, hl, onHover, q }: { r: SearchResult; rank: number; hl: boolean; onHover: (id: string | null) => void; q: string }) {
  const href = (id: string) => `/hotel/${encodeURIComponent(id)}${q ? `?q=${encodeURIComponent(q)}` : ""}`;
  const cmp = useCompare();
  const h = r.hotel;
  return (
    <div className={`card result ${hl ? "hl" : ""}`} onMouseEnter={() => onHover(h.id)} onMouseLeave={() => onHover(null)} id={`r-${h.id}`}>
      <div className="col" style={{ alignItems: "center" }}>
        <ScoreRing score={r.score} />
        <span className="tiny muted">#{rank}</span>
      </div>
      <div>
        <div className="row between wrap" style={{ alignItems: "flex-start" }}>
          <div style={{ flex: "1 1 240px", minWidth: 0 }}>
            <h2 className="result-title"><Link to={href(h.id)}>{h.name}</Link></h2>
            <div className="small muted">{KIND_RU[h.kind] || h.kind}{h.stars ? ` · ${"★".repeat(h.stars)}` : ""}{h.address ? ` · ${h.address}` : ""}{h.levels ? ` · ${h.levels} эт.` : ""}</div>
            {(r.others || []).length > 0 && (
              <div className="tiny mt-s">
                ещё {r.others!.length} {plural(r.others!.length, "корпус", "корпуса", "корпусов")} комплекса:{" "}
                {r.others!.map((o, i) => <span key={o.id}>{i > 0 && ", "}<Link to={href(o.id)}>{o.corpus || o.name}</Link> <span className="muted">({o.score})</span></span>)}
              </div>
            )}
          </div>
          <div className="row wrap">
            <button className={`btn small ${cmp.has(h.id) ? "primary" : ""}`} onClick={() => cmp.toggle(h.id, h.name, q)}>
              {cmp.has(h.id) ? "В сравнении" : "Сравнить"}
            </button>
            <Link className="btn small" to={href(h.id)}>Подробнее</Link>
            <WishButton hotelId={h.id} q={q} small />
          </div>
        </div>
        {r.room && (
          <div className="room">
            <Icon name="compass" size={15} /> <b>Совет агента:</b> {r.room.text}
            {r.room.share_text && <div className="tiny muted">{r.room.share_text}</div>}
          </div>
        )}
        <NeedsList needs={r.needs} />
        {r.verdict && <div className="small muted mt-s">{r.verdict}</div>}
        {r.excluded && <div className="small" style={{ color: "var(--bad)", marginTop: 6 }}>Не выполнено обязательное условие</div>}
        {r.why_above && <div className="why" title="Чем этот отель лучше следующего в выдаче">↑ Выше следующего: {r.why_above}</div>}
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

  const removeCriteria = (idx: number[]) => {
    if (!res) return;
    const crit = res.query.criteria.filter((_, j) => !idx.includes(j));
    if (crit.length === 0) { setRes(null); return; }
    run(text, crit);
  };

  const results = res?.results.filter((r) => !r.excluded) || [];
  return (
    <div className="page wide">
      <div className={`hero ${res ? "compact" : ""}`}>
        <h1>Найдите отель, который подходит именно вам</h1>
        <p className="hero-lead">
          Опишите своими словами, что для вас важно. Агенты знают об отелях Сочи то, чего нет в карточках:
          что видно из окна на каждом этаже, когда виден закат, насколько шумно ночью с каждой стороны здания,
          сколько на самом деле идти до пляжа в горку — и что ответили отельеры.
        </p>
        <form className="searchbox" onSubmit={(e) => { e.preventDefault(); run(text); }}>
          <label className="searchfield">
            <span className="field-label">Что для вас важно в отеле</span>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="например: тихий номер с видом на закат недалеко от моря" autoFocus />
          </label>
          <button className="btn primary" type="submit" disabled={loading}>{loading ? <Spinner /> : <Icon name="search" />} Найти</button>
        </form>
        {!res && <Link to="/tour" className="hero-tour"><Icon name="check" size={15} /> Демо-тур: проверить решение за 5 минут</Link>}
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
              {groupCriteria(res.query.criteria).map(({ idx, crits }) => {
                const c = crits[0];
                return (
                  <span key={idx.join("-")} className={`chip ${c.required ? "warn" : "accent"}`} title={c.source_text ? `из фразы «${c.source_text}»` : undefined}>
                    {critText(crits)}{c.weight > 1 ? " · важно" : c.weight < 1 ? " · желательно" : ""}
                    <button type="button" className="x" onClick={() => removeCriteria(idx)} aria-label={`Убрать условие «${critText(crits)}»`}>✕</button>
                  </span>
                );
              })}
              {res.query.criteria.length === 0 && <span className="muted small">не нашёл знакомых характеристик</span>}
            </div>
            <span className="small muted" title="Корпуса одного комплекса показаны одной карточкой">лучшие {results.length} из {res.total} {plural(res.total, "отеля", "отелей", "отелей")}</span>
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
              <ResultCard key={r.hotel.id} r={r} rank={i + 1} hl={hl === r.hotel.id} onHover={setHl} q={res.query.text} />
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
          <div className="card"><h2 className="h3"><Icon name="sunset" /> Вид и закат по этажам</h2><p className="small muted">Лучи из каждого окна по рельефу Copernicus DEM и высотам 44 тыс. зданий OSM; положение Солнца по месяцам.</p></div>
          <div className="card"><h2 className="h3"><Icon name="moon" /> Тишина с каждой стороны</h2><p className="small muted">Шум дорог, железной дороги и ночных баров с экранированием домами — отдельно для каждого фасада.</p></div>
          <div className="card"><h2 className="h3"><Icon name="chat" /> Агенты спрашивают отельеров</h2><p className="small muted">Чего нет в открытых данных — питомцы, шторы, окна — агент спрашивает и раскладывает свободный ответ по полям.</p></div>
        </div>
      )}
    </div>
  );
}

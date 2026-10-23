import { api } from "../api";
import { ErrorBox, num, pct, plural, SOURCE_RU, Spinner, useAsync } from "../ui";

const HEIGHT_SRC_RU: Record<string, string> = {
  osm_levels: "тег этажности OSM", osm_height: "тег высоты OSM", osm_parts: "3D-части здания в OSM",
  neighbors: "оценка по соседям", default: "по типу здания",
};

function EvalBlock({ title, data, kind, titles }: { title: string; data: any; kind: "answers" | "queries"; titles: Record<string, string> }) {
  if (!data) return null;
  const names = (keys: string[]) => keys.map((k) => titles[k] || k).join(", ") || "—";
  return (
    <div className="card flat">
      <h3>{title}</h3>
      {kind === "answers" ? (
        <div className="small">{data.answers} {plural(data.answers, "ответ", "ответа", "ответов")}, {data.fields} {plural(data.fields, "поле", "поля", "полей")} · точность <b>{pct(data.accuracy, 1)}</b> · precision {pct(data.precision, 1)} · recall {pct(data.recall, 1)}</div>
      ) : (
        <div className="small">{data.queries} {plural(data.queries, "запрос", "запроса", "запросов")} · F1 <b>{num(data.f1, 3)}</b> · precision {pct(data.precision, 1)} · recall {pct(data.recall, 1)} · точное совпадение {pct(data.exact_match, 0)}</div>
      )}
      {data.errors?.length > 0 && (
        <details className="mt-s">
          <summary className="small muted">Ошибки ({data.errors.length})</summary>
          {data.errors.map((e: any, i: number) => (
            <div key={i} className="tiny mt-s">{kind === "answers"
              ? `«${e.answer}» · ${e.field}: ждали ${JSON.stringify(e.expected)}, получили ${JSON.stringify(e.got)}`
              : `«${e.query}» · пропущено: ${names(e.missed)} · лишнее: ${names(e.extra)}`}</div>
          ))}
        </details>
      )}
    </div>
  );
}

export default function MetricsPage() {
  const m = useAsync<any>(() => api.metrics(), []);
  const d = m.data;
  const titles: Record<string, string> = Object.fromEntries((d?.per_characteristic || []).map((c: any) => [c.key, c.title]));
  return (
    <div className="page wide">
      <h1>Метрики качества</h1>
      <p className="muted">Покрытие и достоверность знаний, нагрузка на отельеров, противоречия и точность компонентов на размеченных выборках. Выборки лежат в <span className="mono">data/eval</span>, пересчитать — <span className="mono">python scripts/evaluate.py</span>.</p>
      <ErrorBox error={m.error} />
      {m.loading && <Spinner />}
      {d && (
        <>
          <div className="grid four">
            <div className="card kpi"><div className="v">{pct(d.coverage)}</div><div className="l">покрытие: пар «отель × характеристика» со значением ({num(d.hotels)} {plural(d.hotels, "отель", "отеля", "отелей")} × {d.characteristics} {plural(d.characteristics, "характеристика", "характеристики", "характеристик")})</div></div>
            <div className="card kpi"><div className="v">{pct(d.question_share)}</div><div className="l">доля пар, по которым агент спрашивал отельера · ответов {num(d.questions.answered)} из {num(d.questions.total)}</div></div>
            <div className="card kpi"><div className="v">{d.conflicts.detected}</div><div className="l">противоречий найдено · авто {d.conflicts.resolved_auto} · модератор {d.conflicts.resolved_moderator} · открыто {d.conflicts.open}</div></div>
            <div className="card kpi"><div className="v">{d.stale}</div><div className="l">устаревших значений · поисков {d.searches}</div></div>
          </div>

          <div className="grid two mt">
            <div className="card">
              <h2>Точность на размеченных выборках</h2>
              <p className="small muted">
                «Настроечная» — на ней писались правила; «dev» — вторая выборка, на которой правила дорабатывались; «отложенная» — составлена в конце и не использовалась для настройки: это честная оценка.
                Без LLM работают правила; с ключом LLM запросы и ответы разбирает модель (замер: <span className="mono">scripts/evaluate.py --llm</span>).
              </p>
              <div className="col">
                <EvalBlock title="Разбор ответов отельеров — отложенная выборка" data={d.eval.answers_holdout} kind="answers" titles={titles} />
                <EvalBlock title="Разбор запросов гостей — отложенная выборка" data={d.eval.queries_holdout} kind="queries" titles={titles} />
                <EvalBlock title="Ответы — dev" data={d.eval.answers_dev} kind="answers" titles={titles} />
                <EvalBlock title="Запросы — dev" data={d.eval.queries_dev} kind="queries" titles={titles} />
                <EvalBlock title="Ответы — настроечная" data={d.eval.answers} kind="answers" titles={titles} />
                <EvalBlock title="Запросы — настроечная" data={d.eval.queries} kind="queries" titles={titles} />
              </div>
            </div>
            <div className="card">
              <h2>Проверка геометодов</h2>
              <h3>Положение Солнца (NOAA) против PyEphem</h3>
              <div className="small">Макс. ошибка времени заката <b>{num(d.eval.sun.max_time_error_min, 1)} мин</b>, азимута <b>{num(d.eval.sun.max_azimuth_error_deg, 1)}°</b> на 12 датах. <span className="muted">{d.eval.sun.reference}</span></div>
              <div className="table-scroll"><table className="t mt-s">
                <thead><tr><th>Дата</th><th>Закат</th><th>Эталон</th><th>Азимут</th><th>Эталон</th></tr></thead>
                <tbody>{d.eval.sun.checks.map((c: any) => <tr key={c.date}><td>{c.date}</td><td>{c.sunset}</td><td>{c.ref_sunset}</td><td>{num(c.azimuth, 1)}°</td><td>{num(c.ref_azimuth, 1)}°</td></tr>)}</tbody>
              </table></div>
              {d.eval.levels_imputation && (
                <>
                  <h3 className="mt">Этажность зданий без тега</h3>
                  <div className="small">
                    Кросс-валидация на {num(d.eval.levels_imputation.validated_on)} размеченных зданиях:
                    оценка по соседям — ошибка <b>{num(d.eval.levels_imputation.mae_neighbors, 2)} эт.</b>, в ±1 этаж <b>{pct(d.eval.levels_imputation.within1_neighbors)}</b>;
                    значение по умолчанию — {num(d.eval.levels_imputation.mae_default, 2)} эт. и {pct(d.eval.levels_imputation.within1_default)}.
                  </div>
                  <div className="small muted mt-s">{d.eval.levels_imputation.dsm_check?.note}</div>
                </>
              )}
              {d.eval.demand?.top_share != null && (
                <>
                  <h3 className="mt">Польза вопросов отельерам</h3>
                  <div className="small">
                    Лучшие 20% открытых вопросов ({num(d.eval.demand.top_n)} из {num(d.eval.demand.questions)}) дают <b>{pct(d.eval.demand.top_share)}</b> всей пользы для спроса —
                    прироста позиций отелей в топ-30 при ответе «да»; полезных вопросов — {num(d.eval.demand.useful)}.
                    {" "}Спрос: {d.eval.demand.demand_queries} {plural(d.eval.demand.demand_queries, "запрос", "запроса", "запросов")}, из них демо — {d.eval.demand.demo_queries}.
                  </div>
                </>
              )}
              {d.eval.ranking?.pairs > 0 && (
                <>
                  <h3 className="mt">Согласие ранжирования с разметкой</h3>
                  <div className="small">
                    {d.eval.ranking.pairs} {plural(d.eval.ranking.pairs, "пара", "пары", "пар")} «для запроса Q отель A должен стоять выше B», составленных по фактам до настройки:
                    {" "}<b>{pct(d.eval.ranking.agreement, 1)}</b> пар в правильном порядке.
                  </div>
                  {d.eval.ranking.errors?.length > 0 && (
                    <details className="mt-s">
                      <summary className="small muted">Ошибки ({d.eval.ranking.errors.length})</summary>
                      {d.eval.ranking.errors.map((e: any, i: number) => (
                        <div key={i} className="tiny mono mt-s">«{e.query}»: {e.above} — {e.pos_above}-е место, {e.below} — {e.pos_below}-е</div>
                      ))}
                    </details>
                  )}
                </>
              )}
              <h3 className="mt">Геохарактеристики против ручной разметки</h3>
              {d.eval.geo_gold?.labels ? (
                <div className="small">{d.eval.geo_gold.labels} {plural(d.eval.geo_gold.labels, "отель", "отеля", "отелей")}, {d.eval.geo_gold.fields} {plural(d.eval.geo_gold.fields, "поле", "поля", "полей")} · точность <b>{pct(d.eval.geo_gold.accuracy, 1)}</b></div>
              ) : (
                <div className="small muted">Разметки пока нет: файл <span className="mono">data/eval/geo_gold.yaml</span> заполняется по фото номеров и панорамам.</div>
              )}
              <h3 className="mt">Данные</h3>
              <div className="small">Зданий в модели застройки: {num(d.data_sources.buildings)} · источник высоты: {Object.entries(d.data_sources.height_sources || {}).map(([k, v]) => `${HEIGHT_SRC_RU[k] || k} — ${num(v as number)}`).join(", ")} · POI {num(d.data_sources.pois)} · дорог {num(d.data_sources.roads)}</div>
            </div>
          </div>

          <div className="card mt">
            <h2>По характеристикам</h2>
            <div className="table-scroll"><table className="t">
              <thead><tr><th>Характеристика</th><th>Покрытие</th><th>Ср. уверенность</th><th>Источники</th><th>Вопросов отельерам</th><th>Противоречий</th><th>Устарело</th></tr></thead>
              <tbody>
                {d.per_characteristic.map((c: any) => (
                  <tr key={c.key}>
                    <td><b>{c.title}</b></td>
                    <td style={{ minWidth: 120 }}><div className="meter"><i style={{ width: `${c.coverage * 100}%` }} /></div><span className="tiny muted">{pct(c.coverage)}</span></td>
                    <td>{c.known ? pct(c.avg_confidence) : "—"}</td>
                    <td className="small">{Object.entries(c.sources).map(([s, n]) => `${SOURCE_RU[s] || s}: ${n}`).join(", ") || "—"}</td>
                    <td>{c.asked}</td>
                    <td>{c.conflict}</td>
                    <td>{c.stale}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </div>
        </>
      )}
    </div>
  );
}

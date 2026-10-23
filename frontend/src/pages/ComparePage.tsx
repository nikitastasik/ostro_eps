import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { confColor, ErrorBox, KIND_RU, NeedIcon, Spinner, StatusBadge, useAsync, useCompare } from "../ui";

const GROUP_RU: Record<string, string> = {
  view: "Вид из окна", atmosphere: "Тишина и атмосфера", walk: "Пляж, центр, транспорт", around: "Что рядом",
  building: "Здание", hotelier: "Со слов отельера", custom: "Добавленные",
};

export default function ComparePage() {
  const [params] = useSearchParams();
  const cmp = useCompare();
  const ids = (params.get("ids") || cmp.items.map((x) => x.id).join(",")).split(",").filter(Boolean);
  const q = params.get("q") ?? (params.get("ids") ? "" : cmp.query);
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const data = useAsync<any>(() => (ids.length >= 2 ? api.compare(ids, q) : Promise.resolve(null)), [ids.join(","), q]);

  if (ids.length < 2) {
    return (
      <div className="page">
        <h1>Сравнение отелей</h1>
        <div className="card empty">Добавьте в сравнение хотя бы два отеля — кнопкой «Сравнить» в поиске, в списке отелей или на карточке отеля.</div>
      </div>
    );
  }
  const d = data.data;
  const href = (id: string) => `/hotel/${encodeURIComponent(id)}${q ? `?q=${encodeURIComponent(q)}` : ""}`;
  const rows = d ? d.rows.filter((r: any) => !onlyDiff || !r.same) : [];
  let lastGroup = "";
  const head = (first: string) => d && (
    <thead>
      <tr>
        <th>{first}</th>
        {d.hotels.map((h: any) => (
          <th key={h.id}>
            <Link to={href(h.id)}>{h.name}</Link>
            <div className="tiny muted">{KIND_RU[h.kind] || h.kind}{h.levels ? ` · ${h.levels} эт.` : ""}</div>
          </th>
        ))}
      </tr>
    </thead>
  );
  return (
    <div className="page wide">
      <h1>Сравнение отелей</h1>
      <p className="muted">Короткое значение — в клетке, полный текст с источником — по клику. Зелёным подсвечено лучшее значение в строке, точка — уверенность.</p>
      <ErrorBox error={data.error} />
      {data.loading && <Spinner />}
      {d && d.fit_rows.length > 0 && (
        <div className="card mt">
          <div className="row between wrap">
            <h2 style={{ margin: 0 }}>Под ваш запрос</h2>
            <span className="small muted">«{q}» · <Link to={`/?q=${encodeURIComponent(q)}`}>к выдаче</Link></span>
          </div>
          <p className="tiny muted mt-s">Лучший в строке — по вкладу в оценку: совпадение с учётом уверенности данных и доли подходящих номеров, как в поиске.</p>
          <div className="cmp-scroll mt-s">
            <table className="cmp">
              {head("Пожелание")}
              <tbody>
                {d.fit_rows.map((r: any) => (
                  <tr key={r.key}>
                    <td>{r.label || r.title}</td>
                    {r.cells.map((c: any, i: number) => (
                      <td key={i} className={r.winner === i ? "best" : ""} title={c.text}>
                        <span className="fit-cell"><NeedIcon status={c.status} /> {c.short}</span>
                        {c.status === "conditional" && <span className="cond-tag">с условием</span>}
                      </td>
                    ))}
                  </tr>
                ))}
                <tr className="total">
                  <td>Итоговая оценка</td>
                  {d.fit.map((f: any, i: number) => <td key={i} className={f.best ? "best" : ""}><b>{f.score}</b> из 100</td>)}
                </tr>
                <tr>
                  <td>Совет по номеру</td>
                  {d.fit.map((f: any, i: number) => (
                    <td key={i} className="small">{f.room?.text || "—"}{f.room?.share_text && <div className="tiny muted">{f.room.share_text}</div>}</td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
      {d && (
        <div className="card mt">
          <div className="row between wrap">
            <h2 style={{ margin: 0 }}>Все характеристики</h2>
            <label className="row small" style={{ gap: 6, cursor: "pointer" }}>
              <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> Только различия
            </label>
          </div>
          <div className="cmp-scroll mt-s">
            <table className="cmp">
              {head("Характеристика")}
              <tbody>
                {rows.map((row: any) => {
                  const g = row.group !== lastGroup ? (lastGroup = row.group, true) : false;
                  const expanded = !!open[row.key];
                  return (
                    <Fragment key={row.key}>
                      {g && <tr className="group"><td colSpan={d.hotels.length + 1}>{GROUP_RU[row.group] || row.group}</td></tr>}
                      <tr className="clickable" onClick={() => setOpen({ ...open, [row.key]: !expanded })} title="Показать полный текст">
                        <td>{row.title} <span className="tiny muted">{expanded ? "▴" : "▾"}</span></td>
                        {row.cells.map((c: any, i: number) => (
                          <td key={i} className={c.best ? "best" : ""}>
                            {c.summary ? (
                              <>
                                <span className="dot" style={{ background: confColor(c.confidence), marginRight: 6 }} title={`Уверенность ${Math.round(c.confidence * 100)}%`} />
                                <span className="chip-val">{c.short}</span>
                                {c.status !== "ok" && <> <StatusBadge status={c.status} /></>}
                                {expanded && <div className="small mt-s">{c.summary}</div>}
                              </>
                            ) : <span className="muted">нет данных</span>}
                          </td>
                        ))}
                      </tr>
                    </Fragment>
                  );
                })}
                {rows.length === 0 && <tr><td colSpan={d.hotels.length + 1} className="muted">Все значения совпадают</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="row mt-s">
            {d.hotels.map((h: any) => <button key={h.id} className="btn small ghost" onClick={() => cmp.toggle(h.id, h.name)}>убрать «{h.name}»</button>)}
          </div>
        </div>
      )}
    </div>
  );
}

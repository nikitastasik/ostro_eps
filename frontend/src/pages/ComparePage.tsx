import { Fragment } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { confColor, ErrorBox, KIND_RU, Spinner, StatusBadge, useAsync, useCompare } from "../ui";

const GROUP_RU: Record<string, string> = {
  view: "Вид из окна", atmosphere: "Тишина и атмосфера", walk: "Пляж, центр, транспорт", around: "Что рядом",
  building: "Здание", hotelier: "Со слов отельера", custom: "Добавленные",
};

export default function ComparePage() {
  const [params] = useSearchParams();
  const cmp = useCompare();
  const ids = (params.get("ids") || cmp.items.map((x) => x.id).join(",")).split(",").filter(Boolean);
  const data = useAsync<any>(() => (ids.length >= 2 ? api.compare(ids) : Promise.resolve(null)), [ids.join(",")]);

  if (ids.length < 2) {
    return (
      <div className="page">
        <h1>Сравнение отелей</h1>
        <div className="card empty">Добавьте в сравнение хотя бы два отеля — кнопкой «Сравнить» в поиске, в списке отелей или на карточке отеля.</div>
      </div>
    );
  }
  const d = data.data;
  let lastGroup = "";
  return (
    <div className="page wide">
      <h1>Сравнение отелей</h1>
      <p className="muted">Все характеристики, которые агенты собрали из открытых данных и у отельеров. Зелёным подсвечено лучшее значение в строке, точка — уверенность.</p>
      <ErrorBox error={data.error} />
      {data.loading && <Spinner />}
      {d && (
        <div className="card compare-wrap" style={{ padding: 0 }}>
          <table className="cmp">
            <thead>
              <tr>
                <th>Характеристика</th>
                {d.hotels.map((h: any) => (
                  <th key={h.id}>
                    <Link to={`/hotel/${encodeURIComponent(h.id)}`}>{h.name}</Link>
                    <div className="tiny muted">{KIND_RU[h.kind] || h.kind}{h.levels ? ` · ${h.levels} эт.` : ""}</div>
                    <button className="btn small ghost" onClick={() => cmp.toggle(h.id, h.name)}>убрать</button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.rows.map((row: any) => {
                const head = row.group !== lastGroup ? (lastGroup = row.group, true) : false;
                return (
                  <Fragment key={row.key}>
                    {head && <tr className="group"><td colSpan={d.hotels.length + 1}>{GROUP_RU[row.group] || row.group}</td></tr>}
                    <tr>
                      <td>{row.title}</td>
                      {row.cells.map((c: any, i: number) => (
                        <td key={i} className={c.best ? "best" : ""}>
                          {c.summary ? (
                            <>
                              <span className="dot" style={{ background: confColor(c.confidence), marginRight: 6 }} title={`Уверенность ${Math.round(c.confidence * 100)}%`} />
                              {c.summary}
                              {c.status !== "ok" && <> <StatusBadge status={c.status} /></>}
                            </>
                          ) : <span className="muted">нет данных</span>}
                        </td>
                      ))}
                    </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

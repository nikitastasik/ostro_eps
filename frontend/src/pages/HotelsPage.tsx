import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, HotelListItem } from "../api";
import MapView from "../MapView";
import { ErrorBox, KIND_RU, Spinner, useAsync, useCompare } from "../ui";

export default function HotelsPage() {
  const list = useAsync<{ items: HotelListItem[]; total: number }>(() => api.hotels(), []);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [onlySea, setOnlySea] = useState(false);
  const [hl, setHl] = useState<string | null>(null);
  const nav = useNavigate();
  const cmp = useCompare();
  const items = useMemo(() => (list.data?.items || []).filter((h) =>
    (!q || h.name.toLowerCase().includes(q.toLowerCase()) || (h.address || "").toLowerCase().includes(q.toLowerCase())) &&
    (!kind || h.kind === kind) && (!onlySea || h.badges.sea_view)), [list.data, q, kind, onlySea]);

  return (
    <div className="page wide">
      <div className="row between wrap mb">
        <div>
          <h1>Отели региона</h1>
          <p className="muted">Все объекты размещения центрального Сочи из OpenStreetMap. Значки — то, что агенты узнали сверх карточки.</p>
        </div>
        <div className="row">
          <input className="input" placeholder="Название или адрес" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 170 }}>
            <option value="">Все типы</option>
            {Object.entries(KIND_RU).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label className="chip"><input type="checkbox" checked={onlySea} onChange={(e) => setOnlySea(e.target.checked)} /> с видом на море</label>
        </div>
      </div>
      <ErrorBox error={list.error} />
      {list.loading && <Spinner />}
      <div className="layout-search">
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="t">
            <thead><tr><th>Отель</th><th>Этажей</th><th>Море</th><th>Закат</th><th>Ночью, дБ</th><th>До пляжа</th><th>Покрытие</th><th /></tr></thead>
            <tbody>
              {items.map((h) => (
                <tr key={h.id} onMouseEnter={() => setHl(h.id)} onMouseLeave={() => setHl(null)}>
                  <td><Link to={`/hotel/${encodeURIComponent(h.id)}`}><b>{h.name}</b></Link><div className="tiny muted">{KIND_RU[h.kind] || h.kind}{h.address ? ` · ${h.address}` : ""}</div></td>
                  <td>{h.levels ?? "—"}</td>
                  <td>{h.badges.sea_view ? <span className="badge blue">есть</span> : <span className="muted">—</span>}</td>
                  <td>{h.badges.sunset ? <span className="badge ok">есть</span> : <span className="muted">—</span>}</td>
                  <td>{h.badges.quiet_db != null ? Math.round(h.badges.quiet_db) : "—"}</td>
                  <td className="nowrap">{h.badges.beach_min != null ? (h.badges.beach_min < 1 ? "<1 мин" : `${Math.round(h.badges.beach_min)} мин`) : "—"}</td>
                  <td style={{ minWidth: 90 }}><div className="meter"><i style={{ width: `${h.coverage * 100}%` }} /></div></td>
                  <td><button className={`btn small ${cmp.has(h.id) ? "primary" : ""}`} onClick={() => cmp.toggle(h.id, h.name)}>{cmp.has(h.id) ? "✓" : "+"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length === 0 && !list.loading && <div className="empty">Ничего не найдено</div>}
        </div>
        <div className="sticky">
          <MapView fitKey={`${kind}${onlySea}${items.length}`} markers={items.map((h) => ({
            id: h.id, lat: h.lat, lon: h.lon, color: h.badges.sea_view ? "#0a6cff" : "#7a8799", label: "", title: h.name, highlight: hl === h.id,
          }))} onMarkerClick={(id) => nav(`/hotel/${encodeURIComponent(id)}`)} />
          <p className="tiny muted mt-s">Синие — с видом на море хотя бы с одного этажа (расчёт по рельефу и застройке).</p>
        </div>
      </div>
    </div>
  );
}

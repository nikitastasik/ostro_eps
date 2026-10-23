import { useEffect, useMemo, useState } from "react";
import { api, ViewData } from "./api";
import RaysMap from "./RaysMap";
import { ErrorBox, Spinner } from "./ui";

const CLS_COLOR: Record<number, string> = { 0: "#c9b48a", 1: "#2f86e6", 2: "#8a93a3", 3: "#5fae63", 4: "#8d76b8" };
const CLS_RU: Record<number, string> = { 0: "склоны и холмы", 1: "море", 2: "здания", 3: "деревья", 4: "горы > 450 м" };
const COMPASS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"];
const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const MONTH_COLORS = ["#4a6fd1", "#5b7fd8", "#6f8fd6", "#e3a33b", "#ef9a2f", "#f2852a", "#ef6d2a", "#ee8a2e", "#e9a43d", "#7a8fd6", "#6a7fd2", "#4a63c9"];

function diff(a: number, b: number) {
  return ((a - b + 540) % 360) - 180;
}

export default function Panorama({ hotelId, initialFacade, initialFloor }: { hotelId: string; initialFacade?: string; initialFloor?: number }) {
  const [facade, setFacade] = useState<string | undefined>(initialFacade);
  const [floor, setFloor] = useState<number | undefined>(initialFloor);
  const [month, setMonth] = useState<number>(new Date().getMonth() + 1);
  const [data, setData] = useState<ViewData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.view(hotelId, facade, floor).then(
      (d) => {
        if (!alive) return;
        setData(d);
        setError(null);
        setLoading(false);
        if (!facade) setFacade(d.facade);
        if (!floor) setFloor(d.floor);
      },
      (e) => { if (alive) { setError(e); setLoading(false); } },
    );
    return () => { alive = false; };
  }, [hotelId, facade, floor]);

  const W = 900, H = 330, elMin = -3, elMax = 24;
  const view = useMemo(() => {
    if (!data) return null;
    const n = data.normal_az;
    const span = 140;
    const x = (az: number) => ((diff(az, n) + span / 2) / span) * W;
    const y = (el: number) => H - ((Math.max(elMin, Math.min(elMax, el)) - elMin) / (elMax - elMin)) * H;
    const step = W / data.azimuths.length;
    const bars = data.azimuths.map((az, i) => ({ x: x(az) - step / 2, y: y(data.horizon[i]), cls: data.sky_cls[i], sea: data.sea_vis[i], h: data.horizon[i], d: data.sky_d[i] }));
    const winHalf = data.window_half;
    const inWindow = (az: number) => Math.abs(diff(az, n)) <= winHalf;
    // лучи идут через 1° включая края окна (−60…+60 — это 121 луч), поэтому ограничиваем шириной окна
    const seaWidth = Math.min(2 * winHalf, data.azimuths.filter((az, i) => data.sea_vis[i] && inWindow(az)).length);
    const sunsets = data.suns.map((s) => {
      const dd = diff(s.azimuth, n);
      const visibleSide = Math.abs(dd) <= span / 2;
      const i = data.azimuths.findIndex((az) => Math.abs(diff(az, s.azimuth)) < 0.6);
      const hz = i >= 0 ? data.horizon[i] : 99;
      const cls = i >= 0 ? data.sky_cls[i] : -1;
      const pts = s.path.filter(([, el, az]) => Math.abs(diff(az, n)) <= span / 2 && el > -1).map(([m, el, az]) => {
        const j = data.azimuths.findIndex((a) => Math.abs(diff(a, az)) < 0.6);
        const hidden = j >= 0 ? el < data.horizon[j] : true;
        return { m, el, az, hidden, x: x(az), y: y(el) };
      });
      const full = hz <= 1.0;
      const partial = hz <= 4.0 && (cls === 0 || cls === 4) && (i >= 0 ? data.sky_d[i] >= 1500 : false);
      const seen = visibleSide && Math.abs(dd) <= winHalf && (full || partial);
      return { ...s, dd, visibleSide, hz, cls, pts, seen, overSea: seen && cls === 1 };
    });
    return { x, y, bars, step, seaWidth, sunsets, winHalf, n };
  }, [data]);

  if (error) return <ErrorBox error={error} />;
  if (!data || !view) return <div className="empty"><Spinner /></div>;

  const seenMonths = view.sunsets.filter((s) => s.seen);
  const ticks: number[] = [];
  for (let d = -70; d <= 70; d += 10) ticks.push(d);

  return (
    <div className="pano-wrap">
      <div className="row between wrap mb">
        <div className="facade-tabs">
          {data.facades.map((f) => (
            <button key={f.key} className={f.key === data.facade ? "on" : ""} onClick={() => setFacade(f.key)} title={`Нормаль фасада ${f.normal_az}°`}>
              {f.key} · {Math.round(f.normal_az)}°
            </button>
          ))}
        </div>
        <div className="row" style={{ minWidth: 240 }}>
          <span className="small muted nowrap">Этаж {data.floor}</span>
          <input type="range" min={0} max={data.floors.length - 1} step={1} value={Math.max(0, data.floors.indexOf(data.floor))}
            onChange={(e) => setFloor(data.floors[Number(e.target.value)])} aria-label="Этаж" />
          <select className="month-select" value={month} onChange={(e) => setMonth(Number(e.target.value))} aria-label="Месяц заката">
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>закат: {m}</option>)}
          </select>
          {loading && <Spinner />}
        </div>
      </div>
      <svg className="pano" viewBox={`0 0 ${W} ${H + 26}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8ec5ff" />
            <stop offset="70%" stopColor="#dcefff" />
            <stop offset="100%" stopColor="#fff2db" />
          </linearGradient>
        </defs>
        <rect x={0} y={0} width={W} height={H} fill="url(#sky)" />
        {/* линия горизонта 0° */}
        <line x1={0} x2={W} y1={view.y(0)} y2={view.y(0)} stroke="#ffffff" strokeDasharray="4 4" strokeWidth={1} />
        {/* профиль горизонта по классам */}
        {view.bars.map((b, i) => (
          <rect key={i} x={b.x} y={b.y} width={view.step + 0.6} height={Math.max(0, H - b.y)} fill={CLS_COLOR[b.cls] || "#aaa"} opacity={0.95}>
            <title>{`${Math.round(data.azimuths[i])}°: горизонт ${b.h.toFixed(1)}° — ${CLS_RU[b.cls] || ""}${b.d ? `, ${b.d >= 1000 ? (b.d / 1000).toFixed(1) + " км" : Math.round(b.d) + " м"}` : ""}${b.sea ? "; море видно" : ""}`}</title>
          </rect>
        ))}
        {/* полоса «в этом направлении видно море» */}
        {view.bars.map((b, i) => b.sea ? <rect key={`s${i}`} x={b.x} y={H - 7} width={view.step + 0.6} height={7} fill="#0a5bd8" /> : null)}
        {/* траектории Солнца перед закатом */}
        {view.sunsets.map((s) => s.pts.length > 1 ? (
          <g key={s.month}>
            <polyline points={s.pts.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" stroke={MONTH_COLORS[s.month - 1]}
              strokeWidth={s.month === month ? 4 : s.seen ? 2.4 : 1.4} strokeDasharray={s.seen ? undefined : "4 4"} opacity={s.seen || s.month === month ? 0.95 : 0.55} />
            {(() => {
              const last = s.pts[s.pts.length - 1];
              return (
                <g>
                  <circle cx={last.x} cy={last.y} r={s.seen ? 7 : 4} fill={s.seen ? "#ffb02e" : "#f3d39a"} stroke="white" strokeWidth={1.5} />
                  <text x={Math.min(W - 16, Math.max(16, last.x))} y={last.y - 12 - (s.month % 3) * 12} fontSize={11} textAnchor="middle" fill="#7a4a00" fontWeight={700}>{MONTHS[s.month - 1]}</text>
                </g>
              );
            })()}
          </g>
        ) : null)}
        {/* окно: затемняем то, что вне ±60° */}
        <rect x={0} y={0} width={view.x(view.n - view.winHalf)} height={H} fill="#0b1830" opacity={0.28} />
        <rect x={view.x(view.n + view.winHalf)} y={0} width={W - view.x(view.n + view.winHalf)} height={H} fill="#0b1830" opacity={0.28} />
        <line x1={W / 2} x2={W / 2} y1={0} y2={H} stroke="#ffffff" strokeOpacity={0.7} strokeDasharray="2 5" />
        {/* шкала азимутов */}
        <rect x={0} y={H} width={W} height={26} fill="#ffffff" />
        {ticks.map((t) => {
          const az = (data.normal_az + t + 360) % 360;
          const xx = ((t + 70) / 140) * W;
          const ci = Math.round(az / 45) % 8;
          const isCompass = Math.abs(diff(az, ci * 45)) < 5;
          return (
            <g key={t}>
              <line x1={xx} x2={xx} y1={H} y2={H + 5} stroke="#9aa6b8" />
              <text x={t === -70 ? xx + 3 : t === 70 ? xx - 3 : xx} y={H + 18} fontSize={11}
                textAnchor={t === -70 ? "start" : t === 70 ? "end" : "middle"} fill={isCompass ? "#13213b" : "#8a96a8"} fontWeight={isCompass ? 700 : 400}>
                {isCompass ? COMPASS[ci] : `${Math.round(az)}°`}
              </text>
            </g>
          );
        })}
        {[0, 10, 20].map((e) => (
          <text key={e} x={6} y={view.y(e) - 3} fontSize={10.5} fill="#40516b">{e}°</text>
        ))}
      </svg>
      <div className="legend">
        {[1, 2, 3, 0, 4].map((c) => <span key={c}><i style={{ background: CLS_COLOR[c] }} />{CLS_RU[c]}</span>)}
        <span><i style={{ background: "#0a5bd8" }} />направления, где видно море</span>
        <span><i style={{ background: "#ffb02e", borderRadius: 6 }} />закат по месяцам (сплошная — виден из окна)</span>
        <span><i style={{ background: "#0b1830", opacity: 0.3 }} />вне обзора окна (±{data.window_half}°)</span>
      </div>
      <p className="small mt-s">
        <b>{data.facade}-сторона, {data.floor}-й этаж</b> (глаза на высоте {Math.round(data.eye_z)} м над уровнем моря):{" "}
        {view.seaWidth > 0 ? <>море видно в секторе ≈{view.seaWidth}° из {2 * view.winHalf}° обзора окна. </> : <>моря из этого окна не видно. </>}
        {seenMonths.length > 0
          ? <>Закат виден: {seenMonths.map((s) => `${MONTHS[s.month - 1]}${s.overSea ? " (над морем)" : ""}`).join(", ")}.</>
          : <>Закат из этого окна не виден.</>}
      </p>
      <h3 className="mt">Куда смотрит окно: лучи обзора на карте</h3>
      <p className="small muted">Из окна выбранной стороны и этажа — 120 лучей через 1°: каждый идёт до того, во что упирается взгляд. Красным подсвечены дома, которые закрывают вид; оранжевый пунктир — где садится солнце в выбранном месяце.</p>
      <RaysMap data={data} month={month} />
    </div>
  );
}

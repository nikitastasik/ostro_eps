import type { RayKind, ViewData } from "./api";
import MapView, { MapLine, MapPolygon } from "./MapView";

const KIND_COLOR: Record<RayKind, string> = { sea: "#2f86e6", building: "#e0464b", trees: "#14a05a", land: "#a0763c", mountain: "#7c4ddb" };
const KIND_RU: Record<RayKind, string> = { sea: "море", building: "дома", trees: "деревья", land: "склоны и холмы", mountain: "горы" };
const KIND_ACC: Record<RayKind, string> = { sea: "море", building: "дом", trees: "деревья", land: "склон", mountain: "горы" };
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const SUN_COLOR = "#ff9800";
const MAX_D = 1800;

/** Точка на расстоянии d метров по азимуту az от (lat, lon) — в виде [lon, lat] для MapView. */
function dest(lat: number, lon: number, az: number, d: number): [number, number] {
  const a = (az * Math.PI) / 180;
  return [lon + (d * Math.sin(a)) / (111320 * Math.cos((lat * Math.PI) / 180)), lat + (d * Math.cos(a)) / 111320];
}

function angleDiff(a: number, b: number) {
  return ((a - b + 540) % 360) - 180;
}

/** Закат в выбранном месяце: куда уходит луч на азимут заката и виден ли закат (то же правило, что в панораме). */
export function sunsetRay(data: ViewData, month: number) {
  const sun = data.suns[month - 1];
  if (!sun) return null;
  let i = 0;
  data.azimuths.forEach((az, k) => { if (Math.abs(angleDiff(az, sun.azimuth)) < Math.abs(angleDiff(data.azimuths[i], sun.azimuth))) i = k; });
  const inWindow = Math.abs(angleDiff(sun.azimuth, data.normal_az)) <= data.window_half;
  const hz = data.horizon[i], cls = data.sky_cls[i], d = data.sky_d[i];
  const seen = inWindow && (hz <= 1.0 || (hz <= 4.0 && (cls === 0 || cls === 4) && d >= 1500));
  const ray = data.rays?.find((r) => Math.abs(angleDiff(r.az, sun.azimuth)) < 0.6) || null;
  return { az: sun.azimuth, time: sun.time, inWindow, seen, ray };
}

export default function RaysMap({ data, month }: { data: ViewData; month: number }) {
  const w = data.window;
  if (!w || !data.rays) return null;
  const lines: MapLine[] = [];
  for (const r of data.rays) {
    if (!r.win) continue;
    const end = dest(w.lat, w.lon, r.az, Math.max(r.d, 6));
    lines.push({ points: [[w.lon, w.lat], end], color: KIND_COLOR[r.kind], weight: 1.6, opacity: 0.8, noFit: true });
    if (r.kind === "sea") {
      // луч уходит за берег — продолжаем его в море с затуханием
      lines.push({ points: [end, dest(w.lat, w.lon, r.az, Math.min(MAX_D, r.d + 700))], color: KIND_COLOR.sea, weight: 1.6, opacity: 0.2, noFit: true });
    } else if (r.far) {
      for (const s of [-150, 150]) lines.push({ points: [end, dest(end[1], end[0], r.az + s, 45)], color: KIND_COLOR[r.kind], weight: 1.6, opacity: 0.8, noFit: true });
    }
  }
  // масштаб — по длине лучей в окне: близкие дома-заслоны должны быть видны
  const ds = data.rays.filter((r) => r.win).map((r) => r.d).sort((a, b) => a - b);
  const radius = Math.min(600, Math.max(110, (ds[Math.floor(ds.length / 2)] || 300) * 1.3));
  const sun = sunsetRay(data, month);
  if (sun) {
    lines.push({ points: [[w.lon, w.lat], dest(w.lat, w.lon, sun.az, 1500)], color: SUN_COLOR, weight: 3, dash: "8 6", opacity: 0.95, noFit: true,
      title: `15 ${MONTHS_GEN[month - 1]}: закат в ${sun.time}, азимут ${Math.round(sun.az)}°` });
  }
  const polygons: MapPolygon[] = (data.blockers || []).map((b) => ({
    points: b.polygon.map(([lat, lon]) => [lon, lat] as [number, number]),
    color: b.new ? "#7c4ddb" : "#b3261e", fill: b.new ? "#7c4ddb" : "#e0464b", weight: 2,
    title: `${b.new ? "Новый дом из сценария: " : ""}${b.label} — закрывает ${b.deg}° обзора`,
  }));
  let sunText = "";
  if (sun) {
    const head = `15 ${MONTHS_GEN[month - 1]} солнце садится на азимуте ${Math.round(sun.az)}°`;
    if (!sun.inWindow) sunText = `${head} — вне обзора этого окна: закат отсюда не виден`;
    else if (sun.seen && sun.ray?.kind === "sea") sunText = `${head} — луч уходит в море: закат виден`;
    else if (sun.seen) sunText = `${head} — над горизонтом ничего не мешает: закат виден`;
    else if (sun.ray) sunText = `${head} — луч упирается в ${KIND_ACC[sun.ray.kind]} в ${sun.ray.d} м: закат не виден`;
    else sunText = `${head} — закат не виден`;
  }
  return (
    <div className="rays">
      <MapView className="map rays-map" lines={lines} polygons={polygons} focus={{ lat: w.lat, lon: w.lon, radius }}
        fitKey={`${data.facade}-${data.floor}`} drawKey={`${data.facade}-${data.floor}-${month}-${(data.blockers || []).map((b) => b.id).join(",")}`}
        markers={[{ id: "window", lat: w.lat, lon: w.lon, color: "#13213b", label: "", size: 12, title: `Окно: ${data.facade}-сторона, ${data.floor}-й этаж` }]} />
      <div className="legend">
        {(Object.keys(KIND_COLOR) as RayKind[]).map((k) => <span key={k}><i style={{ background: KIND_COLOR[k] }} />{KIND_RU[k]}</span>)}
        <span><i style={{ background: SUN_COLOR }} />закат в выбранном месяце</span>
        <span><i style={{ background: "#e0464b", opacity: 0.5 }} />дома, которые закрывают вид</span>
      </div>
      {data.rays_caption && <p className="small mt-s"><b>{data.rays_caption}.</b>{sunText ? ` ${sunText}.` : ""}</p>}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import type { Need } from "./api";

// ---------------------------------------------------------------- иконки (простые штриховые SVG)
const PATHS: Record<string, string> = {
  waves: "M2 12c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 17c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 7c2-2 4-2 6 0s4 2 6 0 4-2 6 0",
  sunset: "M12 3v4M4.2 7.2l2.8 2.8M19.8 7.2 17 10M3 16h18M7 16a5 5 0 0 1 10 0M5 20h14",
  mountain: "M3 20 10 8l4 6 2-3 5 9z",
  landmark: "M12 2l3 6H9zM9 8h6v13H9zM6 21h12",
  building: "M5 21V4h10v17M15 9h4v12M8 7h4M8 11h4M8 15h4",
  moon: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
  party: "M4 20l5-14 9 9zM13 4l1 2M18 6l-2 1M20 10h-2",
  crane: "M4 21h8M8 21V5h12M8 5l-4 4M17 5v5M16 10h2v3h-2z",
  beach: "M3 20h18M12 4c-4 0-7 3-7 6h14c0-3-3-6-7-6zM12 10v10",
  walk: "M13 4a2 2 0 1 1 0 .01M10 22l2-7 3 3v6M8 10l3-2 3 2 3 2M11 8l-1 5",
  train: "M6 4h12v11H6zM6 10h12M9 19l-2 3M15 19l2 3M9 15h.01M15 15h.01",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  kids: "M9 6a2 2 0 1 1 0 .01M16 8a1.6 1.6 0 1 1 0 .01M7 21v-6l-2-3 4-3 2 3-2 3M15 21v-5l-2-2 3-2 2 2-1 2",
  car: "M3 16v-4l2-5h14l2 5v4zM7 16v2M17 16v2M5 12h14",
  paw: "M8 10a1.6 1.6 0 1 1 0 .01M16 10a1.6 1.6 0 1 1 0 .01M5 14a1.4 1.4 0 1 1 0 .01M19 14a1.4 1.4 0 1 1 0 .01M12 14c3 0 5 3 4 5s-6 2-8 0 1-5 4-5z",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l4 2",
  thermometer: "M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z",
  floors: "M4 20h16M6 20V8l6-4 6 4v12M10 12h4M10 16h4",
  curtains: "M3 3h18M5 3v18M19 3v18M5 3c3 4 3 10 0 18M19 3c-3 4-3 10 0 18",
  window: "M4 4h16v16H4zM12 4v16M4 12h16",
  laptop: "M5 6h14v9H5zM2 18h20",
  elevator: "M5 3h14v18H5zM9 10l3-3 3 3M9 14l3 3 3-3",
  wheelchair: "M11 4a1.5 1.5 0 1 1 0 .01M11 7v6h5l2 5M8 10a6 6 0 1 0 8 7",
  compass: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM15 9l-2 5-4 1 2-5z",
  pin: "M12 21s7-6.2 7-11a7 7 0 0 0-14 0c0 4.8 7 11 7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  chat: "M4 5h16v11H8l-4 4z",
  dot: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  agents: "M12 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM5 22v-3a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v3",
  refresh: "M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5",
  check: "M5 12l5 5 9-10",
  x: "M6 6l12 12M18 6 6 18",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  bolt: "M13 2 4 14h7l-1 8 9-12h-7z",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  layers: "M12 3 2 8l10 5 10-5zM2 13l10 5 10-5M2 18l10 5 10-5",
};

export function Icon({ name, size = 18, stroke = 1.8 }: { name?: string; size?: number; stroke?: number }) {
  const d = PATHS[name || "dot"] || PATHS.dot;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

// ---------------------------------------------------------------- статусы, уверенность, источники
const STATUS_RU: Record<string, string> = {
  ok: "готово", conflict: "противоречие", stale: "устарело", unknown: "нет данных", asked: "ждём отельера",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{STATUS_RU[status] || status}</span>;
}

export function confColor(c: number) {
  return c >= 0.7 ? "#14a05a" : c >= 0.45 ? "#e38b00" : "#e0464b";
}

export const NEED_STATUS: Record<string, { icon: string; label: string }> = {
  match: { icon: "✓", label: "подходит" },
  conditional: { icon: "✓", label: "подходит с условием" },
  partial: { icon: "~", label: "не дотягивает" },
  miss: { icon: "✕", label: "не подходит" },
  unknown: { icon: "?", label: "нет данных" },
};

/** Значок статуса пожелания: ✓ / ✓ с условием / ~ / ✕ / ?. */
export function NeedIcon({ status }: { status: string }) {
  const s = NEED_STATUS[status] || NEED_STATUS.unknown;
  return <span className={`ic ${status}`} title={s.label}>{s.icon}</span>;
}

/** Пожелания гостя с итогом по каждому: значок статуса, объяснение, источники и уверенность. */
export function NeedsList({ needs }: { needs: Need[] }) {
  return (
    <div className="needs">
      {needs.map((n) => (
        <div className="need" key={n.key}>
          <NeedIcon status={n.status} />
          <div>
            <b>{n.title}:</b> <span className={n.status === "unknown" ? "muted" : ""}>{n.text || "—"}</span>
            {n.status === "conditional" && <span className="cond-tag">с условием</span>}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <Sources sources={n.sources} />
            {n.status !== "unknown" && <Confidence value={n.confidence} label={false} />}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Confidence({ value, label = true }: { value: number; label?: boolean }) {
  return (
    <span className="conf" title={`Уверенность ${Math.round(value * 100)}%`}>
      <span className="bar"><i style={{ width: `${Math.round(value * 100)}%`, background: confColor(value) }} /></span>
      {label && <span>{Math.round(value * 100)}%</span>}
    </span>
  );
}

export const SOURCE_RU: Record<string, string> = {
  geo: "геоданные", osm: "OSM", osm_estimate: "оценка по соседям", climate: "NOAA", hotelier: "отельер",
  hotelier_confirmed: "подтверждено отельером", moderator: "модератор", default: "допущение", dsm: "DSM",
};

export function Sources({ sources }: { sources: string[] }) {
  if (!sources?.length) return null;
  return (
    <span className="row wrap" style={{ gap: 4 }}>
      {sources.map((s) => (
        <span key={s} className={`src ${s}`}>{SOURCE_RU[s] || s}</span>
      ))}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" />;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error">{error instanceof Error ? error.message : String(error)}</div>;
}

// ---------------------------------------------------------------- форматирование
export function fmtDate(iso?: string | null, withTime = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", withTime
    ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
    : { day: "numeric", month: "short", year: "numeric" });
}

/** Форма слова для числа: 1 вопрос, 3 вопроса, 5 вопросов. */
export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
}

export function pct(v: number | null | undefined, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export const KIND_RU: Record<string, string> = {
  hotel: "Отель", guest_house: "Гостевой дом", hostel: "Хостел", apartment: "Апартаменты", motel: "Мотель", chalet: "Шале",
};

export function scoreColor(s: number) {
  if (s >= 75) return "#14a05a";
  if (s >= 55) return "#6aa72f";
  if (s >= 40) return "#e38b00";
  return "#e0464b";
}

export function ScoreRing({ score }: { score: number }) {
  const c = scoreColor(score);
  return (
    <div className="score" style={{ background: `conic-gradient(${c} ${score * 3.6}deg, #e8edf4 0deg)` }} title="Насколько отель подходит под запрос с учётом уверенности данных">
      <div style={{ position: "absolute", inset: 6, borderRadius: "50%", background: "white" }} />
      <span>{score}</span>
    </div>
  );
}

// ---------------------------------------------------------------- хуки
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const counter = useRef(0);
  const reload = useCallback(() => {
    const n = ++counter.current;
    setLoading(true);
    fn().then(
      (d) => { if (n === counter.current) { setData(d); setError(null); setLoading(false); } },
      (e) => { if (n === counter.current) { setError(e); setLoading(false); } },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}

export function useToast() {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(null), 3200);
    return () => clearTimeout(t);
  }, [msg]);
  const node = msg ? <div className="toast">{msg}</div> : null;
  return { show: setMsg, node };
}

// ---------------------------------------------------------------- сравнение (выбранные отели)
const CMP_KEY = "ostrovok_compare";

export function getCompare(): { id: string; name: string }[] {
  try {
    return JSON.parse(localStorage.getItem(CMP_KEY) || "[]");
  } catch {
    return [];
  }
}

export function setCompare(items: { id: string; name: string }[]) {
  try {
    localStorage.setItem(CMP_KEY, JSON.stringify(items.slice(0, 5)));
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event("compare-changed"));
}

const CMP_Q_KEY = "ostrovok_compare_q";

/** Запрос, из выдачи которого добавляли отели в сравнение: сравнение начинается с блока «Под ваш запрос». */
export function getCompareQuery(): string {
  try {
    return localStorage.getItem(CMP_Q_KEY) || "";
  } catch {
    return "";
  }
}

function setCompareQuery(q: string) {
  try {
    if (q) localStorage.setItem(CMP_Q_KEY, q);
    else localStorage.removeItem(CMP_Q_KEY);
  } catch {
    /* ignore */
  }
}

export function useCompare() {
  const [items, setItems] = useState(getCompare());
  useEffect(() => {
    const h = () => setItems(getCompare());
    window.addEventListener("compare-changed", h);
    return () => window.removeEventListener("compare-changed", h);
  }, []);
  const toggle = (id: string, name: string, q?: string) => {
    const cur = getCompare();
    if (q !== undefined) setCompareQuery(q);
    if (cur.find((x) => x.id === id)) setCompare(cur.filter((x) => x.id !== id));
    else setCompare([...cur, { id, name }]);
  };
  const clear = () => {
    setCompareQuery("");
    setCompare([]);
  };
  return { items, toggle, has: (id: string) => !!items.find((x) => x.id === id), clear, query: getCompareQuery() };
}

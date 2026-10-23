// Клиент API и типы данных.

export type Status = "ok" | "conflict" | "stale" | "unknown" | "asked";

export interface Fact {
  id: number;
  char_key: string;
  source: string;
  source_ru: string;
  source_detail: string;
  method: string;
  summary: string;
  confidence: number;
  observed_at: string;
  valid_until: string;
  stale: boolean;
  status: string;
  evidence: Record<string, unknown>;
  value: Record<string, unknown> | null;
}

export interface CharValue {
  key: string;
  title: string;
  group: string;
  scope: string;
  icon?: string;
  description: string;
  origin?: string;
  status: Status;
  summary: string;
  confidence: number;
  sources: string[];
  sources_ru?: string[];
  value: Record<string, any> | null;
  explanation: string;
  updated_at: string | null;
  valid_until: string | null;
  stale: boolean;
  fact_ids?: number[];
}

export interface Group {
  key: string;
  title: string;
  items: CharValue[];
}

export interface Question {
  id: number;
  hotel_id: string;
  hotel_name?: string;
  char_keys: string[];
  titles?: string[];
  text: string;
  reason: string;
  status: string;
  answer_text: string | null;
  parsed: Record<string, any> | null;
  parse_method: string | null;
  answered_by: string | null;
  created_at: string;
  answered_at: string | null;
}

export interface Conflict {
  id: number;
  hotel_id: string;
  hotel_name: string;
  char_key: string;
  title: string;
  field: string;
  description: string;
  status: string;
  resolution: string | null;
  resolved_by: string | null;
  created_at: string;
  facts: Fact[];
}

export interface Step {
  idx: number;
  ts: string;
  agent: string;
  kind: string;
  char_key: string | null;
  message: string;
  payload: Record<string, any>;
  duration_ms: number | null;
}

export interface Run {
  id: number;
  kind: string;
  trigger: string;
  hotel_id: string | null;
  hotel_name?: string | null;
  char_keys: string[];
  status: string;
  summary: string;
  error: string | null;
  params: Record<string, any>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  steps?: Step[];
}

export interface HotelCard {
  id: string;
  name: string;
  kind: string;
  stars: number | null;
  lat: number;
  lon: number;
  address: string | null;
  website: string | null;
  phone: string | null;
  osm: string | null;
  osm_name?: string;
  merged_from?: { id: string; osm: string; kind: string; dist_m: number; moved: string[] }[];
  complex?: { id: string; corpus: string | null; siblings: { id: string; label: string; corpus: string | null }[] } | null;
  building: { levels: number | null; levels_src: string | null; height: number | null; osm: string | null };
  groups: Group[];
  coverage: number;
  known: number;
  total: number;
  questions?: Question[];
  conflicts?: Conflict[];
  runs?: Run[];
  changes?: { char_key: string; old: string; new: string; reason: string; at: string }[];
  strengths?: { key: string; title: string; percentile: number; summary: string }[];
  search_appearances?: { count: number; searches: number; items: { query: string; at: string; rank: number }[] };
}

export interface HotelListItem {
  id: string;
  name: string;
  kind: string;
  stars: number | null;
  lat: number;
  lon: number;
  address: string | null;
  levels: number | null;
  coverage: number;
  badges: { sea_view: boolean; sunset: boolean; quiet_db: number | null; beach_min: number | null };
}

export interface Criterion {
  key: string;
  field: string;
  op: string;
  value: any;
  soft?: number | null;
  weight: number;
  required: boolean;
  month?: number | null;
  month_field?: string | null;
  label: string;
  source_text?: string;
}

export interface Need {
  key: string;
  title: string;
  status: "match" | "conditional" | "partial" | "miss" | "unknown";
  match: number | null;
  floor?: number | null;
  share?: number | null;
  confidence: number;
  text: string;
  sources: string[];
  rv_status: string;
  label: string;
}

export interface SearchResult {
  hotel: { id: string; name: string; kind: string; stars: number | null; lat: number; lon: number; address: string | null; levels: number | null; corpus?: string | null };
  others?: { id: string; name: string; corpus: string | null; score: number }[];
  share?: number | null;
  why_above?: string | null;
  verdict?: string;
  score: number;
  excluded: boolean;
  needs: Need[];
  room: { facade: string; facade_text: string; min_floor: number | null; category?: string; text: string; share?: number; share_text?: string } | null;
  summary: string;
  caveats: number;
  known: number;
}

export interface SearchResponse {
  query: { text: string; criteria: Criterion[]; months: number[]; unmatched: string[]; parser: string };
  results: SearchResult[];
  total: number;
  criteria_keys: string[];
  llm: boolean;
}

export interface Meta {
  region: { name: string; bbox: number[]; origin: { lat: number; lon: number }; sources: Record<string, any>; stats: Record<string, any>; center: any };
  hotels: number;
  characteristics: number;
  groups: { key: string; title: string }[];
  llm: { enabled: boolean; model: string | null; last_error: string | null };
  tools: { name: string; title: string; agent: string; sources: string[]; description: string; generic: boolean }[];
  examples: string[];
}

export interface ViewData {
  facade: string;
  facades: { key: string; normal_az: number }[];
  normal_az: number;
  floor: number;
  floors: number[];
  window_half: number;
  azimuths: number[];
  horizon: number[];
  sky_cls: number[];
  sky_d: number[];
  sea_vis: boolean[];
  suns: { month: number; azimuth: number; time: string; path: [number, number, number][] }[];
  eye_z: number;
  ground: number;
  window?: { lat: number; lon: number };
  rays?: { az: number; kind: RayKind; d: number; far: boolean; b: number | null; win: boolean }[];
  blockers?: { id: number; deg: number; dist_m: number; levels: number | null; height_m: number; street: string | null; new: boolean; label: string; polygon: [number, number][] }[];
  ray_counts?: Record<RayKind, number>;
  rays_caption?: string;
}

export type RayKind = "sea" | "building" | "trees" | "land" | "mountain";

export interface WishData {
  hotel: SearchResult["hotel"];
  query: string;
  text: string;
  text_rules: string;
  method: "rules" | "llm";
  reasons: { title: string; status: string; text: string; sources: string[]; confidence: number }[];
  questions: string[];
  room: SearchResult["room"];
  share_text?: string;
}

export interface GeoData {
  id: string;
  lat: number;
  lon: number;
  footprint: [number, number][] | null;
  facades: { key: string; normal_az: number; lat: number; lon: number; night_db?: number; sea?: boolean; sunset?: boolean; [k: string]: any }[];
  routes: Record<string, { line: [number, number][]; profile: [number, number][]; summary: string }>;
}

const TOKEN_KEY = "ostrovok_hotelier_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* приватный режим */
  }
}

async function request<T>(path: string, init?: RequestInit, auth = false): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (auth) {
    const t = getToken();
    if (t) headers.Authorization = `Bearer ${t}`;
  }
  const r = await fetch(path, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string>) } });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = j.detail || detail;
    } catch {
      /* not json */
    }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail)) as Error & { status?: number };
    err.status = r.status;
    throw err;
  }
  return r.json();
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  hotels: (q = "") => request<{ items: HotelListItem[]; total: number }>(`/api/hotels?q=${encodeURIComponent(q)}`),
  hotel: (id: string) => request<HotelCard>(`/api/hotels/${encodeURIComponent(id)}`),
  facts: (id: string, key?: string) =>
    request<{ items: Fact[] }>(`/api/hotels/${encodeURIComponent(id)}/facts${key ? `?char_key=${key}` : ""}`),
  geo: (id: string) => request<GeoData>(`/api/hotels/${encodeURIComponent(id)}/geo`),
  wish: (id: string, q: string) => request<WishData>(`/api/hotels/${encodeURIComponent(id)}/wish?q=${encodeURIComponent(q)}`),
  fit: (id: string, q: string) =>
    request<{ query: SearchResponse["query"]; result: SearchResult }>(`/api/hotels/${encodeURIComponent(id)}/fit?q=${encodeURIComponent(q)}`),
  view: (id: string, facade?: string, floor?: number) =>
    request<ViewData>(
      `/api/hotels/${encodeURIComponent(id)}/view?${facade ? `facade=${encodeURIComponent(facade)}&` : ""}${floor ? `floor=${floor}` : ""}`,
    ),
  collect: (id: string, char_keys?: string[]) =>
    request<Run>(`/api/hotels/${encodeURIComponent(id)}/collect`, { method: "POST", body: JSON.stringify({ char_keys }) }),
  runs: (params = "") => request<{ items: Run[] }>(`/api/runs${params}`),
  run: (id: number, after = -1) => request<Run>(`/api/runs/${id}?after=${after}`),
  search: (query: string, criteria?: Criterion[]) =>
    request<SearchResponse>("/api/search", { method: "POST", body: JSON.stringify({ query, criteria }) }),
  compare: (ids: string[]) => request<any>(`/api/compare?ids=${ids.map(encodeURIComponent).join(",")}`),
  characteristics: () => request<{ items: any[]; groups: any[] }>("/api/characteristics"),
  compile: (text: string) => request<{ spec: any; method: string; errors: string[] }>("/api/characteristics/compile", { method: "POST", body: JSON.stringify({ text }) }),
  createChar: (text: string, spec?: any) => request<{ spec: any; method: string; run: Run }>("/api/characteristics", { method: "POST", body: JSON.stringify({ text, spec }) }),
  toggleChar: (key: string) => request<{ key: string; enabled: boolean }>(`/api/characteristics/${key}/toggle`, { method: "POST" }),
  collectChar: (key: string) => request<Run>(`/api/characteristics/${key}/collect`, { method: "POST" }),
  questions: (params = "") => request<{ items: Question[] }>(`/api/questions${params}`),
  simulate: (qid: number) => request<{ run: Run; question: Question }>(`/api/questions/${qid}/simulate`, { method: "POST" }),
  conflicts: (params = "") => request<{ items: Conflict[] }>(`/api/conflicts${params}`),
  resolveConflict: (id: number, keep_fact_id: number, note = "") =>
    request<Conflict>(`/api/conflicts/${id}/resolve`, { method: "POST", body: JSON.stringify({ keep_fact_id, note }) }),
  changes: () => request<{ items: any[] }>("/api/changes"),
  overview: () => request<any>("/api/agents/overview"),
  actualize: () => request<Run>("/api/agents/actualize", { method: "POST" }),
  age: (days: number) => request<{ facts_aged: number; became_stale: number }>("/api/agents/age", { method: "POST", body: JSON.stringify({ days }) }),
  scenarioBuilding: (body: any) => request<{ override: any; run: Run }>("/api/scenarios/building", { method: "POST", body: JSON.stringify(body) }),
  scenarioReset: () => request<any>("/api/scenarios", { method: "DELETE" }),
  metrics: () => request<any>("/api/metrics"),
  login: (login: string, password: string) =>
    request<{ token: string; login: string }>("/api/auth/login", { method: "POST", body: JSON.stringify({ login, password }) }),
  hotelierHotels: (q = "") => request<{ items: any[] }>(`/api/hotelier/hotels?q=${encodeURIComponent(q)}`, undefined, true),
  hotelierHotel: (id: string) => request<HotelCard>(`/api/hotelier/hotels/${encodeURIComponent(id)}`, undefined, true),
  answer: (qid: number, text: string, preview = false) =>
    request<any>(`/api/hotelier/questions/${qid}/answer`, { method: "POST", body: JSON.stringify({ text, preview }) }, true),
  confirm: (hid: string, key: string) =>
    request<CharValue>(`/api/hotelier/hotels/${encodeURIComponent(hid)}/confirm/${key}`, { method: "POST" }, true),
  correct: (hid: string, key: string, text: string) =>
    request<any>(`/api/hotelier/hotels/${encodeURIComponent(hid)}/correct/${key}`, { method: "POST", body: JSON.stringify({ text }) }, true),
  sea: () => request<any>("/api/region/sea"),
};

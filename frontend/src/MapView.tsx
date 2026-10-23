import L from "leaflet";
import { useEffect, useRef } from "react";

export interface MapMarker {
  id: string;
  lat: number;
  lon: number;
  label?: string;
  color?: string;
  title?: string;
  highlight?: boolean;
  size?: number;
}

export interface MapLine {
  points: [number, number][]; // [lon, lat]
  color: string;
  weight?: number;
  dash?: string;
  title?: string;
  noFit?: boolean;
  opacity?: number;
}

export interface MapPolygon {
  points: [number, number][]; // [lon, lat]
  color: string;
  fill?: string;
  title?: string;
  weight?: number;
}

interface Props {
  markers?: MapMarker[];
  lines?: MapLine[];
  polygons?: MapPolygon[];
  arrows?: { lat: number; lon: number; az: number; color: string; title?: string; label?: string }[];
  onMarkerClick?: (id: string) => void;
  onMapClick?: (lat: number, lon: number) => void;
  className?: string;
  fitKey?: string;
  /** Перерисовать слои при смене содержимого (а не только количества) */
  drawKey?: string;
  /** Показать окрестность точки вместо всех объектов */
  focus?: { lat: number; lon: number; radius: number };
  center?: [number, number];
  zoom?: number;
}

let seaCache: any = null;
let seaPromise: Promise<any> | null = null;

function loadSea() {
  if (seaCache) return Promise.resolve(seaCache);
  if (!seaPromise) {
    seaPromise = fetch("/api/region/sea").then((r) => (r.ok ? r.json() : null)).then((j) => (seaCache = j)).catch(() => null);
  }
  return seaPromise;
}

export default function MapView({ markers = [], lines = [], polygons = [], arrows = [], onMarkerClick, onMapClick, className = "map", fitKey, drawKey, focus, center, zoom }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const lastFit = useRef<string | undefined>("__none__");
  const clickRef = useRef(onMapClick);
  const markerClickRef = useRef(onMarkerClick);
  clickRef.current = onMapClick;
  markerClickRef.current = onMarkerClick;

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = L.map(ref.current, { zoomControl: true, attributionControl: true, preferCanvas: true }).setView(center || [43.59, 39.73], zoom || 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    const seaLayer = L.layerGroup().addTo(map);
    loadSea().then((sea) => {
      if (!sea || !mapRef.current) return;
      L.geoJSON(sea, { style: { color: "#2f86e6", weight: 0, fillColor: "#7fb8f0", fillOpacity: 0.35 }, interactive: false }).addTo(seaLayer);
    });
    layerRef.current = L.layerGroup().addTo(map);
    map.on("click", (e: L.LeafletMouseEvent) => clickRef.current?.(e.latlng.lat, e.latlng.lng));
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();
    const bounds: L.LatLngExpression[] = [];
    for (const p of polygons) {
      const latlngs = p.points.map(([lon, lat]) => [lat, lon] as [number, number]);
      const poly = L.polygon(latlngs, { color: p.color, weight: p.weight ?? 2, fillColor: p.fill || p.color, fillOpacity: 0.35 });
      if (p.title) poly.bindTooltip(p.title);
      poly.addTo(layer);
      latlngs.forEach((ll) => bounds.push(ll));
    }
    for (const ln of lines) {
      const latlngs = ln.points.map(([lon, lat]) => [lat, lon] as [number, number]);
      const pl = L.polyline(latlngs, { color: ln.color, weight: ln.weight ?? 4, dashArray: ln.dash, opacity: ln.opacity ?? 0.9 });
      if (ln.title) pl.bindTooltip(ln.title, { sticky: true });
      pl.addTo(layer);
      if (!ln.noFit) latlngs.forEach((ll) => bounds.push(ll));
    }
    for (const a of arrows) {
      const html = `<div style="transform: rotate(${a.az}deg); width:34px; height:34px; display:grid; place-items:center">
        <svg width="26" height="26" viewBox="0 0 24 24"><path d="M12 2 19 20 12 16 5 20z" fill="${a.color}" stroke="white" stroke-width="1.5"/></svg></div>`;
      const icon = L.divIcon({ html, className: "", iconSize: [34, 34], iconAnchor: [17, 17] });
      const m = L.marker([a.lat, a.lon], { icon, interactive: !!a.title });
      if (a.title) m.bindTooltip(a.title, { direction: "top" });
      m.addTo(layer);
      if (a.label) {
        const t = L.divIcon({ html: `<div style="font-weight:800;font-size:11px;color:${a.color};text-shadow:0 0 3px white,0 0 3px white">${a.label}</div>`, className: "", iconSize: [40, 14], iconAnchor: [-12, 7] });
        L.marker([a.lat, a.lon], { icon: t, interactive: false }).addTo(layer);
      }
    }
    for (const m of markers) {
      const sz = m.size ?? 30;
      const icon = L.divIcon({
        html: `<div class="mk ${m.highlight ? "hl" : ""}" style="background:${m.color || "#0a6cff"}${m.size ? `;width:${sz}px;height:${sz}px;border-width:2px` : ""}">${m.label ?? ""}</div>`,
        className: "", iconSize: [sz, sz], iconAnchor: [sz / 2, sz / 2],
      });
      const mk = L.marker([m.lat, m.lon], { icon, zIndexOffset: m.highlight ? 1000 : 0 });
      if (m.title) mk.bindTooltip(m.title, { direction: "top", offset: [0, -14] });
      mk.on("click", () => markerClickRef.current?.(m.id));
      mk.addTo(layer);
      bounds.push([m.lat, m.lon]);
    }
    if (focus) {
      const dLat = focus.radius / 111320;
      const dLon = focus.radius / (111320 * Math.cos((focus.lat * Math.PI) / 180));
      bounds.length = 0;
      bounds.push([focus.lat - dLat, focus.lon - dLon], [focus.lat + dLat, focus.lon + dLon]);
    }
    if (bounds.length && lastFit.current !== fitKey) {
      lastFit.current = fitKey;
      try {
        map.fitBounds(L.latLngBounds(bounds as L.LatLngTuple[]), { padding: [30, 30], maxZoom: 17 });
      } catch {
        /* ignore */
      }
    }
    // перерисовка размеров (если контейнер появился из скрытого состояния)
    setTimeout(() => map.invalidateSize(), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, drawKey, JSON.stringify(markers.map((m) => [m.id, m.highlight, m.color])), lines.length, polygons.length, arrows.length]);

  return <div ref={ref} className={className} />;
}

"use client";

import maplibregl from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnswerText, citationOrder } from "@/components/AnswerText";
import { Legend } from "@/components/Legend";
import { PermitPanel } from "@/components/PermitPanel";
import {
  activeToday, appNow, askAgent, category, extent, HOOD, isMinor, isOver, MINOR_ZOOM, PARK, isListing, COLOR, daysAway, flagDataUrl, getJSON, HIGHLIGHT, LABEL, LISTED_BY, listingWhere, locate, PINNED_NOW, ROAD, shortWhen, STRENGTH,
  strengthBand, streetCase, tidy, todayStatus, wktToGeometry,
  type AgentReply, type Category, type Extent, type PennEvent, type View,
} from "@/lib/api";

// OpenFreeMap Positron: a quiet vector basemap with street names and no house numbers or shop labels.
const STYLE = "https://tiles.openfreemap.org/styles/positron";
// Center City, river to river and Spring Garden to South St.
const CENTER_CITY: [[number, number], [number, number]] = [[-75.1830, 39.9415], [-75.1415, 39.9620]];
const MAX_CITED_ON_MAP = 30;
const DATA_LAYERS = ["listings", "full", "lane", "sidewalk", "parking", "points", "points-minor", "cited-line"];
// Lines by how much of the street they take, heaviest first; a no-parking curb line last.
const LINE_RANK = (e: PennEvent) => (category(e) === "parking" ? 3 : { full: 0, lane: 1, sidewalk: 2 }[extent(e)]);
const FLAG_ICON = "penn-flag";
// The icon is drawn at 2x for sharp edges; 22 x 27 px on the map. Its pole's foot sits 3 px in from the left.
const FLAG_PX = { w: 44, h: 54, ratio: 2, footX: 3 };
const isPointGeom = (e: PennEvent) => /^(MULTI)?POINT/.test(e.geometry_wkt);
// What a click or hover picks first where things overlap: a flag, then a dot, then lines by severity.
// A marker is an explicit target; the street line under it can still be clicked beside it.
const pickOrder = (e: PennEvent) => (isListing(category(e)) ? 0 : isPointGeom(e) ? 1 : 2 + LINE_RANK(e));
// Neighborhood outlines: a slimmed copy of data/areas/philadelphia-neighborhoods.geojson (OpenDataPhilly),
// the same boundaries the server uses when a question names a neighborhood.
const HOODS_URL = "/neighborhoods.geojson";
const HOOD_LAYERS = ["hood-line", "hood-focus", "hood-label"];
const SIGN = "#1E5B44";   // --sign in globals.css
const PRESETS = [{ days: 1, label: "24 hours" }, { days: 7, label: "7 days" }, { days: 30, label: "30 days" }];

type Status = "idle" | "locating" | "reading" | "done" | "error";
type Place = { name: string | null; window?: string };

const zoomWidth = (lo: number, hi: number) =>
  ["interpolate", ["linear"], ["zoom"], 12, lo, 17, hi] as maplibregl.ExpressionSpecification;
const hoverWidth = (lo: number, hi: number) =>
  ["interpolate", ["linear"], ["zoom"],
    12, ["case", ["boolean", ["feature-state", "hover"], false], lo + 2, lo],
    17, ["case", ["boolean", ["feature-state", "hover"], false], hi + 3, hi]] as maplibregl.ExpressionSpecification;
const isLine = ["!=", ["geometry-type"], "Point"] as maplibregl.FilterSpecification;
const isPoint = ["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]] as maplibregl.FilterSpecification;
const ofCat = (c: Category) => ["all", isLine, ["==", ["get", "cat"], c]] as maplibregl.FilterSpecification;
// A closure line of any reason at one weight (no-parking curb lines have their own layer).
const ofExtent = (x: Extent) => ["all", isLine, ["!=", ["get", "cat"], "parking"], ["==", ["get", "ext"], x]] as maplibregl.FilterSpecification;
// Full strength in effect today, then one step fainter per band of days away (STRENGTH in lib/api.ts).
const todayOpacity = ["step", ["get", "days"], STRENGTH[0].opacity,
  ...STRENGTH.slice(1).flatMap(s => [s.from, s.opacity])] as unknown as maplibregl.ExpressionSpecification;
const flagOpacity = ["max", 0.45, todayOpacity] as maplibregl.ExpressionSpecification;
// Color says why: construction, an event or a block party (or a no-parking curb).
const REASON_COLOR = ["match", ["get", "cat"], "event", COLOR.event, "blockparty", COLOR.blockparty,
  "parking", COLOR.parking, COLOR.construction] as maplibregl.ExpressionSpecification;

/** The map's own window. Questions resolve their own dates on the server and never change it. */
function range(days: number): { start: string; end: string } {
  const now = appNow();
  return { start: now.toISOString(), end: new Date(now.getTime() + days * 864e5).toISOString() };
}

function toFeatures(events: PennEvent[]): GeoJSON.Feature[] {
  return events.flatMap(e => {
    const g = wktToGeometry(e.geometry_wkt);
    return g ? [{ type: "Feature" as const, geometry: g, properties: { event_id: e.event_id, cat: category(e), ext: extent(e), days: daysAway(e) } }] : [];
  });
}

function boundsOf(features: GeoJSON.Feature[]): maplibregl.LngLatBounds | null {
  const b = new maplibregl.LngLatBounds();
  let any = false;
  for (const f of features) {
    const g = f.geometry as GeoJSON.Point | GeoJSON.MultiPoint | GeoJSON.LineString | GeoJSON.MultiLineString;
    const pts = g.type === "Point" ? [g.coordinates] : g.type === "LineString" || g.type === "MultiPoint" ? g.coordinates : g.coordinates.flat();
    pts.forEach(p => { b.extend(p as [number, number]); any = true; });
  }
  return any ? b : null;
}

const reduceMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isNarrow = () => typeof window !== "undefined" && window.matchMedia("(max-width: 720px)").matches;

export default function Page() {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const known = useRef<Map<string, PennEvent>>(new Map());
  const dockRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const pinMarker = useRef<maplibregl.Marker | null>(null);
  const askSeq = useRef(0);
  const loadSeq = useRef(0);

  const [win, setWin] = useState(7);
  const [events, setEvents] = useState<PennEvent[]>([]);
  const [mapLoading, setMapLoading] = useState(true);
  const [mapError, setMapError] = useState("");

  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [place, setPlace] = useState<Place | null>(null);
  const [reply, setReply] = useState<AgentReply | null>(null);
  const [sheetOpen, setSheetOpen] = useState(true);
  const [citedIds, setCitedIds] = useState<string[]>([]);
  const [pin, setPin] = useState<[number, number] | null>(null);   // [lon, lat]
  const [picking, setPicking] = useState(false);

  const [showHoods, setShowHoods] = useState(true);
  const showHoodsRef = useRef(showHoods); showHoodsRef.current = showHoods;
  const [panelOpen, setPanelOpen] = useState(false);
  const [choices, setChoices] = useState<PennEvent[]>([]);
  const [selected, setSelected] = useState<PennEvent | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [hover, setHover] = useState<{ x: number; y: number; e: PennEvent } | null>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 720px)");
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  // Handlers registered once on the map read the latest state through refs.
  const winRef = useRef(win); winRef.current = win;
  const pickingRef = useRef(picking); pickingRef.current = picking;
  const panelOpenRef = useRef(panelOpen); panelOpenRef.current = panelOpen;

  /** Room the floating UI takes, so a fitted place lands in the visible part of the map. */
  const padding = useCallback((expectSheet = false): maplibregl.PaddingOptions => {
    const narrow = isNarrow();
    // The map ends at the ask banner; the answer sheet and legend sit over its bottom edge. While an
    // answer loads, its sheet is still short, so plan for the height it will grow to (see .sheet max-height).
    const grown = expectSheet ? (narrow ? 0.48 * window.innerHeight : Math.min(0.46 * window.innerHeight, 460)) : 0;
    const dock = Math.max(dockRef.current?.getBoundingClientRect().height ?? 0, grown);
    const panel = panelOpenRef.current && !narrow ? 380 : 0;
    return { top: narrow ? 64 : 100, left: narrow ? 20 : 48, right: panel + (narrow ? 20 : 64), bottom: dock + 20 };
  }, []);

  const fly = useCallback((b: maplibregl.LngLatBoundsLike, maxZoom = 17, expectSheet = false) => {
    map.current?.fitBounds(b, { padding: padding(expectSheet), maxZoom, duration: reduceMotion() ? 0 : 1600, essential: true });
  }, [padding]);

  const load = useCallback(() => {
    const m = map.current;
    if (!m || !m.getSource("events")) return;
    const seq = ++loadSeq.current;
    const b = m.getBounds();
    const { start, end } = range(winRef.current);
    setMapLoading(true);
    getJSON<{ events: PennEvent[] }>(`/map?min_lat=${b.getSouth()}&min_lon=${b.getWest()}&max_lat=${b.getNorth()}` +
      `&max_lon=${b.getEast()}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)
      .then(({ events }) => {
        if (seq !== loadSeq.current) return;
        events.forEach(e => known.current.set(e.event_id, e));
        setEvents(events);
        setMapError("");
        (m.getSource("events") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: toFeatures(events) });
      })
      .catch(err => { if (seq === loadSeq.current) setMapError(`The map could not load permits: ${err.message}. Check that the PENN API is running.`); })
      .finally(() => { if (seq === loadSeq.current) setMapLoading(false); });
  }, []);

  const select = useCallback((e: PennEvent, opts: { focus?: boolean } = {}) => {
    setSelected(e);
    setPanelOpen(true);
    setDetailError("");
    const m = map.current;
    const features = toFeatures([e]);
    (m?.getSource("selected") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
    if (opts.focus && m) {
      const b = boundsOf(features);
      const view = m.getBounds();
      if (b && !(view.contains(b.getSouthWest()) && view.contains(b.getNorthEast()))) {
        panelOpenRef.current = true;
        fly(b, 17);
      }
    }
    setDetailLoading(true);
    getJSON<PennEvent>(`/events/${encodeURIComponent(e.event_id)}`)
      .then(full => { known.current.set(full.event_id, full); setSelected(cur => cur?.event_id === full.event_id ? full : cur); })
      .catch(err => setDetailError(`Showing the copy loaded with the map; could not refresh it: ${err.message}.`))
      .finally(() => setDetailLoading(false));
  }, [fly]);

  const closePanel = useCallback(() => {
    setPanelOpen(false);
    setSelected(null);
    setChoices([]);
    (map.current?.getSource("selected") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: [] });
  }, []);

  // ---- map setup ----
  useEffect(() => {
    if (!mapEl.current || map.current) return;
    const m = new maplibregl.Map({
      container: mapEl.current, style: STYLE, bounds: CENTER_CITY,
      fitBoundsOptions: { padding: { top: 60, bottom: 90, left: 20, right: 20 } },
      minZoom: 10, maxZoom: 19, attributionControl: false,
    });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    // The legend owns the bottom-right corner, so the credits go under the zoom buttons.
    m.addControl(new maplibregl.AttributionControl({ compact: true }), "top-right");
    // A compact attribution opens expanded on load; start it folded to its "i" button.
    m.once("idle", () => m.getContainer().querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show"));

    const init = () => {
      if (m.getSource("events")) return;
      const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
      m.addSource("events", { type: "geojson", data: empty, promoteId: "event_id" });
      m.addSource("cited", { type: "geojson", data: empty });
      m.addSource("selected", { type: "geojson", data: empty });
      const flag = new Image(FLAG_PX.w, FLAG_PX.h);
      flag.onload = () => { if (!m.hasImage(FLAG_ICON)) m.addImage(FLAG_ICON, flag, { pixelRatio: FLAG_PX.ratio }); };
      flag.src = flagDataUrl(FLAG_PX.w, FLAG_PX.h);
      // Under the basemap's labels, so street names stay readable on top of the permits.
      const below = m.getStyle().layers.find(l => l.type === "symbol")?.id;
      const add = (l: maplibregl.LayerSpecification) => m.addLayer(l, below);
      const round = { "line-cap": "round", "line-join": "round" } as const;
      // Positron draws every road near-white; give highways and major streets their own look.
      const paint = (id: string, v: string) => { if (m.getLayer(id)) m.setPaintProperty(id, "line-color", v); };
      ["highway_motorway_inner", "highway_motorway_bridge_inner", "tunnel_motorway_inner"].forEach(id => paint(id, ROAD.highway));
      ["highway_motorway_casing", "highway_motorway_bridge_casing", "tunnel_motorway_casing"].forEach(id => paint(id, ROAD.highwayEdge));
      // Every other road is one plain street, at the local-street width (see ROAD in lib/api.ts for why).
      ["highway_minor", "highway_major_inner", "highway_major_casing"].forEach(id => paint(id, ROAD.street));
      const streetWidth = m.getLayer("highway_minor") && m.getPaintProperty("highway_minor", "line-width");
      if (streetWidth) ["highway_major_inner", "highway_major_casing"].forEach(id => m.getLayer(id) && m.setPaintProperty(id, "line-width", streetWidth));

      // Neighborhoods under everything else of ours: a soft green band (no dashes, so it never reads as a permit),
      // and the asked-about one as a solid sign-green line.
      m.addSource("hoods", { type: "geojson", data: HOODS_URL });
      add({ id: "hood-line", type: "line", source: "hoods",
        paint: { "line-color": SIGN, "line-width": zoomWidth(4, 9), "line-opacity": 0.16, "line-blur": 1.5 } });
      add({ id: "hood-focus", type: "line", source: "hoods", filter: ["==", ["get", "name"], ""],
        paint: { "line-color": SIGN, "line-width": zoomWidth(2.5, 4.5), "line-opacity": 0.9 } });

      // The answer's records: a highlighter stroke under the street, then the record's own line.
      add({ id: "cited-glow", type: "line", source: "cited", layout: round,
        paint: { "line-color": HIGHLIGHT, "line-width": zoomWidth(12, 30), "line-opacity": 0.85, "line-blur": 1 } });
      add({ id: "selected-halo", type: "line", source: "selected", layout: round,
        paint: { "line-color": "#1B2433", "line-width": zoomWidth(9, 18), "line-opacity": 0.9 } });
      // Color says why (REASON_COLOR), weight says how much of the street: dots for the sidewalk, a thin line for one
      // lane, a thick white-edged line for the full street. Lightest first, so a full closure draws over a sidewalk
      // permit on the same block. The minor kinds start at MINOR_ZOOM (lib/api.ts), so the whole-city view shows the
      // closures.
      add({ id: "parking", type: "line", source: "events", filter: ofCat("parking"), minzoom: MINOR_ZOOM,
        // A pair of thin rails, like a marked curb lane: a shape of its own beside the sidewalk's dots.
        paint: { "line-opacity": todayOpacity, "line-color": REASON_COLOR, "line-width": hoverWidth(1, 1.8),
          "line-gap-width": zoomWidth(2.2, 5) } });
      add({ id: "sidewalk", type: "line", source: "events", filter: ofExtent("sidewalk"), minzoom: MINOR_ZOOM, layout: { "line-cap": "round" },
        paint: { "line-opacity": todayOpacity, "line-color": REASON_COLOR, "line-width": hoverWidth(1.8, 4.5), "line-dasharray": [0.05, 2] } });
      add({ id: "lane", type: "line", source: "events", filter: ofExtent("lane"), minzoom: MINOR_ZOOM, layout: round,
        paint: { "line-opacity": todayOpacity, "line-color": REASON_COLOR, "line-width": hoverWidth(1.8, 4.5) } });
      // Where full closures overlap, an event draws over a block party, and both over construction.
      const fullOrder = { ...round, "line-sort-key": ["match", ["get", "cat"], "event", 2, "blockparty", 1, 0] } as maplibregl.LineLayerSpecification["layout"];
      add({ id: "full-casing", type: "line", source: "events", filter: ofExtent("full"), layout: round,
        paint: { "line-opacity": todayOpacity, "line-color": "#ffffff", "line-width": zoomWidth(5.5, 13) } });
      add({ id: "full", type: "line", source: "events", filter: ofExtent("full"), layout: fullOrder,
        paint: { "line-opacity": todayOpacity, "line-color": REASON_COLOR, "line-width": hoverWidth(3.5, 9) } });
      // The answer's own lines over every city layer, a little heavier than the rest, so a cited line still reads
      // clearly on its highlighter stroke.
      add({ id: "cited-line", type: "line", source: "cited", filter: isLine, layout: round,
        paint: { "line-opacity": todayOpacity, "line-color": ["get", "color"], "line-width": zoomWidth(3.5, 8) } });
      const dot: maplibregl.CircleLayerSpecification["paint"] = { "circle-opacity": todayOpacity, "circle-stroke-opacity": todayOpacity,
        "circle-color": REASON_COLOR, "circle-radius": zoomWidth(3.5, 7), "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 };
      const isDot = ["==", ["geometry-type"], "Point"] as maplibregl.ExpressionSpecification;
      // A no-parking, one-lane or sidewalk-only spot is minor, like its line (isMinor in lib/api.ts).
      const minorDot = ["any", ["==", ["get", "cat"], "parking"], ["!=", ["get", "ext"], "full"]] as maplibregl.ExpressionSpecification;
      add({ id: "points-minor", type: "circle", source: "events", minzoom: MINOR_ZOOM,
        filter: ["all", isDot, minorDot] as maplibregl.FilterSpecification, paint: dot });
      add({ id: "points", type: "circle", source: "events",
        filter: ["all", isDot, ["!", ["in", ["get", "cat"], ["literal", ["listing", "ticketed"]]]], ["!", minorDot]] as maplibregl.FilterSpecification, paint: dot });
      // Below the basemap's labels, so street names win where they collide.
      add({ id: "hood-label", type: "symbol", source: "hoods", minzoom: 12,
        layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Italic"], "text-size": zoomWidth(11, 14),
          "text-max-width": 8, "text-letter-spacing": 0.04 },
        paint: { "text-color": "#2F5E4C", "text-halo-color": "rgba(255,255,255,0.9)", "text-halo-width": 1.5 } });

      // Listings (visitphilly): flags over everything, street names included, so a flag on a closed street
      // stays visible and clickable. Under each flag's foot, a highlighter dot when cited and a ring when open.
      m.addLayer({ id: "cited-point", type: "circle", source: "cited", filter: isPoint,
        paint: { "circle-color": HIGHLIGHT, "circle-radius": zoomWidth(9, 14), "circle-opacity": 0.85, "circle-blur": 0.2 } });
      m.addLayer({ id: "selected-point", type: "circle", source: "selected", filter: isPoint,
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": zoomWidth(8, 12), "circle-stroke-color": "#1B2433", "circle-stroke-width": 2.5 } });
      m.addLayer({ id: "listings", type: "symbol", source: "events", filter: ["==", ["get", "cat"], "listing"],
        layout: { "icon-image": FLAG_ICON, "icon-anchor": "bottom-left", "icon-offset": [-FLAG_PX.footX, 0],
          "icon-allow-overlap": true, "icon-ignore-placement": true, "symbol-sort-key": ["-", 0, ["get", "days"]] },   // today's on top
        paint: { "icon-opacity": flagOpacity } });
      showHoodsOn(m, showHoodsRef.current);
      load();
    };
    m.on("style.load", init);
    m.on("load", init);

    let hovered: string | null = null;
    const setHovered = (id: string | null) => {
      if (hovered === id) return;
      if (hovered) m.setFeatureState({ source: "events", id: hovered }, { hover: false });
      hovered = id;
      if (id) m.setFeatureState({ source: "events", id }, { hover: true });
    };
    const hits = (p: maplibregl.Point) => {
      const r = isNarrow() ? 12 : 6;   // fingers are wider than a cursor
      const fs = m.queryRenderedFeatures([[p.x - r, p.y - r], [p.x + r, p.y + r]], { layers: DATA_LAYERS.filter(l => m.getLayer(l)) });
      const seen = new Set<string>();
      const found = fs.flatMap(f => {
        const id = f.properties?.event_id as string | undefined;
        if (!id || seen.has(id)) return [];
        seen.add(id);
        const e = known.current.get(id);
        return e ? [e] : [];
      }).sort((a, b) => pickOrder(a) - pickOrder(b));
      // On a flag or dot, only the markers under the cursor count: the street line beneath is not what was aimed at.
      return found.length && pickOrder(found[0]) < 2 ? found.filter(e => pickOrder(e) < 2) : found;
    };

    m.on("mousemove", ev => {
      if (pickingRef.current) { m.getCanvas().style.cursor = "crosshair"; return; }
      const h = hits(ev.point);
      m.getCanvas().style.cursor = h.length ? "pointer" : "";
      setHovered(h[0] && known.current.has(h[0].event_id) ? h[0].event_id : null);
      setHover(h.length && !isNarrow() ? { x: ev.point.x, y: ev.point.y, e: h[0] } : null);
    });
    m.on("mouseout", () => { setHovered(null); setHover(null); });
    m.on("click", ev => {
      if (pickingRef.current) {
        setPin([ev.lngLat.lng, ev.lngLat.lat]);
        setPicking(false);
        return;
      }
      const h = hits(ev.point);
      if (!h.length) { closePanel(); return; }
      setHover(null);
      if (h.length === 1) { setChoices([]); select(h[0]); return; }
      setChoices(h);
      setSelected(null);
      setPanelOpen(true);
      (m.getSource("selected") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: toFeatures(h) });
    });
    let t: ReturnType<typeof setTimeout>;
    m.on("moveend", () => { clearTimeout(t); t = setTimeout(load, 150); });
    return () => { clearTimeout(t); m.remove(); map.current = null; };   // StrictMode mounts twice in development
  }, [load, select, closePanel]);

  useEffect(() => { load(); }, [win, load]);

  useEffect(() => { if (map.current?.getLayer("hood-line")) showHoodsOn(map.current, showHoods); }, [showHoods]);

  // Outline the neighborhood an answer is about, when the place is one ("Fairmount", "Logan Square").
  useEffect(() => {
    const m = map.current;
    if (!m?.getLayer("hood-focus")) return;
    m.setFilter("hood-focus", ["==", ["downcase", ["get", "name"]], (place?.name ?? "").toLowerCase()]);
  }, [place]);

  // The pin is the place for the next questions until it is cleared.
  useEffect(() => {
    const m = map.current;
    pinMarker.current?.remove();
    pinMarker.current = null;
    if (m && pin) pinMarker.current = new maplibregl.Marker({ color: "#1B2433" }).setLngLat(pin).addTo(m);
  }, [pin]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (picking) setPicking(false);
        else if (panelOpenRef.current) closePanel();
      } else if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picking, closePanel]);

  // ---- asking ----
  async function showCited(ids: string[], fitIfNoPlace: boolean) {
    const m = map.current;
    const got = await Promise.all(ids.slice(0, MAX_CITED_ON_MAP).map(id =>
      known.current.get(id) ?? getJSON<PennEvent>(`/events/${encodeURIComponent(id)}`).catch(() => null)));
    const evs = got.filter((e): e is PennEvent => !!e);
    evs.forEach(e => known.current.set(e.event_id, e));
    const features = toFeatures(evs).map(f => ({ ...f, properties: { ...f.properties, color: COLOR[f.properties!.cat as Category] } }));
    (m?.getSource("cited") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
    const b = fitIfNoPlace ? boundsOf(features) : null;
    if (b) fly(b, 16.5);
  }

  async function ask(q: string, at: [number, number] | null = pin) {
    q = q.trim();
    if (!q) return;
    const seq = ++askSeq.current;
    const stale = () => seq !== askSeq.current;
    setAsked(q); setStatus("locating"); setReply(null); setSheetOpen(true); setCitedIds([]); setPlace(null);
    (map.current?.getSource("cited") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: [] });

    // The place resolves in milliseconds without the model: move the map while the answer is written.
    const agent = askAgent(at ? { question: q, lon: at[0], lat: at[1] } : { question: q });
    let flew = false;
    let placeView: View | null = null;
    if (at) {
      flew = true;
      map.current?.flyTo({ center: at, zoom: Math.max(map.current.getZoom(), 16), padding: padding(true), duration: reduceMotion() ? 0 : 1400 });
      setPlace({ name: "The point you picked" });
      setStatus("reading");
    } else {
      locate(q).then(l => {
        if (stale()) return;
        if (l.found && l.view) { flew = true; placeView = l.view; fly(l.view.bounds, 17, true); setPlace({ name: l.place, window: l.window }); }
        setStatus(s => (s === "locating" ? "reading" : s));
      }).catch(() => { if (!stale()) setStatus(s => (s === "locating" ? "reading" : s)); });
    }

    try {
      const r = await agent;
      if (stale()) return;
      setReply(r);
      setStatus("done");
      if (r.needs_pin) return;   // the sheet offers an optional pin; nothing is forced
      if (r.outside) return;
      if (r.place || r.window) setPlace(p => ({ name: r.place ?? p?.name ?? null, window: r.window ?? p?.window }));
      const view: View | null | undefined = r.view;
      if (!flew && view) { flew = true; fly(view.bounds, 17, true); }
      // Settle once the answer has its real height, so the place sits clear of the sheet.
      const settle = placeView ?? view;
      if (settle) requestAnimationFrame(() => requestAnimationFrame(() => { if (!stale()) fly(settle.bounds); }));
      const ids = r.answer ? Array.from(new Set([...citationOrder(r.answer), ...(r.cited ?? [])])) : r.cited ?? [];
      setCitedIds(ids);
      if (ids.length) await showCited(ids, !flew);
    } catch (err) {
      if (stale()) return;
      setStatus("error");
      setReply({ message: `PENN could not answer: ${(err as Error).message}. Check that the PENN API is running, then ask again.` });
    }
  }

  // A pin picked because PENN asked for one answers the waiting question.
  useEffect(() => {
    if (pin && reply?.needs_pin && asked) ask(asked, pin);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pin]);

  const onCite = (id: string) => {
    const e = known.current.get(id);
    if (e) { setChoices([]); select(e, { focus: true }); return; }
    getJSON<PennEvent>(`/events/${encodeURIComponent(id)}`)
      .then(full => { known.current.set(full.event_id, full); setChoices([]); select(full, { focus: true }); })
      .catch(() => setDetailError(`Record ${id} is not in PENN's data.`));
  };

  const counts = events.reduce<Partial<Record<Category, number>>>((acc, e) => {
    const c = category(e); acc[c] = (acc[c] ?? 0) + 1; return acc;
  }, {});
  const bands = events.reduce<number[]>((acc, e) => { acc[strengthBand(daysAway(e))]++; return acc; }, STRENGTH.map(() => 0));
  const titles = Object.fromEntries(citedIds.map(id => [id, tidy(known.current.get(id)?.title ?? "")]).filter(([, t]) => t));
  const order = reply?.answer ? citationOrder(reply.answer) : [];
  const citedNumber = (id: string) => order.indexOf(id) + 1;
  const busy = status === "locating" || status === "reading";
  const hasSheet = status !== "idle";
  const kindNote = reply?.window_kind === "future" ? "Upcoming dates: some permits may not be filed yet."
    : reply?.window_kind === "past" ? "Past dates: this is what the records show was permitted." : "";

  return (
    <main className={`app${panelOpen ? " panel-open" : ""}${picking ? " picking" : ""}`}>
      <div className="map" ref={mapEl} role="region" aria-label="Map of Philadelphia street permits" />

      <header className="topbar">
        <div className="brand">
          <span className="wordmark">PENN</span>
          <span className="tagline">Philadelphia Events and Neighborhood Navigator</span>
        </div>
        <div className="segmented" role="radiogroup" aria-label="Map shows permits active in the next">
          <span className="seg-label" aria-hidden="true">Map shows the next</span>
          {PRESETS.map(p => (
            <button key={p.days} type="button" role="radio" aria-checked={win === p.days}
              onClick={() => setWin(p.days)}>{p.label}</button>
          ))}
        </div>
      </header>

      {mapError && <div className="map-error" role="alert">{mapError}</div>}
      {picking && <div className="pick-hint" role="status">Click the map where you mean. Esc cancels.</div>}

      {hover && (
        <div className="hover-tip" style={{ left: hover.x + 14, top: hover.y + 14 }} aria-hidden="true">
          <TipBody e={hover.e} />
        </div>
      )}

      <PermitPanel open={panelOpen} choices={choices} event={selected} loading={detailLoading} error={detailError}
        citedNumber={citedNumber} onPick={e => select(e)} onBack={choices.length > 1 ? () => setSelected(null) : null}
        onClose={closePanel} />

      <div className="dock">
        <div className="dock-row" ref={dockRef}>
          {hasSheet && (
            <section className={`sheet${sheetOpen ? "" : " collapsed"}`} aria-live="polite" aria-busy={busy}>
              <div className="sheet-head">
                {place?.name ? <span className="plate">{place.name}</span>
                  : busy ? <span className="plate pending">Finding the place</span> : null}
                <span className="sheet-when">{reply?.window ?? place?.window ?? ""}</span>
                <button type="button" className="text-btn sheet-toggle" aria-expanded={sheetOpen}
                  onClick={() => setSheetOpen(o => !o)}>{sheetOpen ? "Hide answer" : "Show answer"}</button>
              </div>
              {sheetOpen && (
                <div className="sheet-body">
                  <p className="asked">{asked}</p>
                  {busy && (
                    <div className="working" role="status">
                      <span className="sweep" aria-hidden="true" />
                      {status === "locating" ? "Finding the place on the map" : `Reading permits${place?.name ? ` near ${place.name}` : ""}`}
                    </div>
                  )}
                  {reply?.needs_pin && (
                    <div className="sheet-msg">
                      <p>PENN could not find a place in your question. Add a corner, like &ldquo;Kelly Dr and Fairmount
                        Ave&rdquo;, or a neighborhood, like &ldquo;Fairmount&rdquo;. Or pick a point on the map to search around it.</p>
                      <button type="button" className="text-btn" onClick={() => setPicking(true)}>Pick a point on the map</button>
                    </div>
                  )}
                  {reply?.message && !reply.needs_pin && <p className={reply.outside || status === "error" ? "sheet-error" : "sheet-msg"}>{reply.message}</p>}
                  {reply?.answer && (
                    <>
                      {kindNote && <p className="kind-note">{kindNote}</p>}
                      <AnswerText text={reply.answer} titles={titles} selected={selected?.event_id ?? null} onCite={onCite} web={reply.web} />
                      {order.length > 0 && <p className="sheet-foot">Numbers open the permit. Highlighted streets on the map are the ones cited.</p>}
                    </>
                  )}
                </div>
              )}
            </section>
          )}
          <Legend counts={counts} bands={bands} citedCount={citedIds.length} hoods={showHoods} onHoods={setShowHoods}
            loading={mapLoading && !events.length} />
        </div>

        <form className="askbar" onSubmit={e => { e.preventDefault(); if (!busy) ask(question); }}>
          <label htmlFor="q" className="sr-only">Ask PENN about a street or corner</label>
          {pin ? (
            <span className="pin-chip">
              Pinned point
              <button type="button" aria-label="Clear the pinned point" onClick={() => setPin(null)}>
                <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 2l6 6m0-6L2 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
              </button>
            </span>
          ) : (
            <button type="button" className={`pick-btn${picking ? " on" : ""}`} aria-pressed={picking}
              title="Pick a point on the map" aria-label="Pick a point on the map" onClick={() => setPicking(p => !p)}>
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 16s5-4.6 5-8.6A5 5 0 0 0 4 7.4C4 11.4 9 16 9 16z" fill="none" stroke="currentColor" strokeWidth="1.6" /><circle cx="9" cy="7.4" r="1.8" fill="currentColor" /></svg>
            </button>
          )}
          <input id="q" ref={inputRef} value={question} onChange={e => setQuestion(e.target.value)} autoComplete="off"
            placeholder={pin ? "Ask about the pinned point" : narrow ? "Ask about a street or corner" : "Ask about a street or corner, like Spruce and 15th next week"} />
          <button type="submit" className="ask-btn" disabled={!question.trim() || busy}>{busy ? "Asking" : "Ask"}</button>
        </form>
      </div>
    </main>
  );
}

/** Our neighborhood outlines replace Positron's OpenStreetMap neighborhood names, which use other boundaries. */
function showHoodsOn(m: maplibregl.Map, on: boolean) {
  HOOD_LAYERS.forEach(id => m.getLayer(id) && m.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  if (m.getLayer("label_other")) m.setLayoutProperty("label_other", "visibility", on ? "none" : "visible");
}

const MAX_TIP_PLACES = 2;

/** The hover card: category, a short title, the first blocks, and when. Long permits list dozens of blocks. */
// How much of the street a construction permit takes, after its reason on the hover card. A block party and an
// event closure close the street; no parking is a curb rule.
const HOW_MUCH: Record<Extent, string> = { full: "full street closed", lane: "one lane closed", sidewalk: "sidewalk only" };

function TipBody({ e }: { e: PennEvent }) {
  const cat = category(e);
  if (cat === "listing") return <ListingTip e={e} />;
  const places = streetCase(tidy(e.description || "")).split(/;\s*/).filter(Boolean);
  const title = tidy(e.title).replace(/\s*extend(?:ed)? from permit\s*#?\s*[\d-]+/gi, "");
  const more = places.length - MAX_TIP_PLACES;
  const unit = places.every(p => /\bblock\b/i.test(p)) ? "block" : "location";
  return (
    <>
      <span className={`tip-cat cat-${cat}`}>{LABEL[cat]}{cat === "construction" && <span className="tip-src">, {HOW_MUCH[extent(e)]}</span>}</span>
      {title && <span className="tip-title">{title.length > 70 ? `${title.slice(0, 68).trimEnd()}…` : title}</span>}
      {places.length > 0 && (
        <ul className={`tip-places cat-${cat}`}>
          {places.slice(0, MAX_TIP_PLACES).map((p, i) => <li key={i}>{p}</li>)}
          {more > 0 && <li className="tip-more">+{more} more {unit}{more === 1 ? "" : "s"}</li>}
        </ul>
      )}
      <span className="tip-foot">
        <span className="tip-when">{todayStatus(e)}</span>
        <span className="tip-hint">Click for details</span>
      </span>
    </>
  );
}

/** A listed event: what, when and where as labelled rows, and who listed it. */
function ListingTip({ e }: { e: PennEvent }) {
  const { where, note } = listingWhere(e);
  const title = tidy(e.title);
  return (
    <>
      <span className="tip-cat cat-listing">{LABEL.listing}<span className="tip-src"> · {LISTED_BY[e.source] ?? e.source}</span></span>
      <span className="tip-title">{title.length > 70 ? `${title.slice(0, 68).trimEnd()}…` : title}</span>
      <dl className="tip-rows">
        <dt>When</dt><dd>{shortWhen(e)}{note && <span className="tip-note">{note}</span>}</dd>
        {where && <><dt>Where</dt><dd>{where}</dd></>}
      </dl>
      <span className="tip-foot">
        <span className="tip-when">{todayStatus(e)}</span>
        <span className="tip-hint">Click for details</span>
      </span>
    </>
  );
}

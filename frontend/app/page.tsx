"use client";

import maplibregl from "maplibre-gl";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { AnswerText, citationOrder } from "@/components/AnswerText";
import { Legend } from "@/components/Legend";
import { PermitPanel } from "@/components/PermitPanel";
import {
  activeToday, appNow, askAgent, category, extent, HOOD, isMinor, isOver, MINOR_ZOOM, PARK, isListing, COLOR, daysAway, flagDataUrl, getJSON, HIGHLIGHT, LABEL, LISTED_BY, listingWhere, locate, PINNED_NOW, ROAD, shortWhen, STRENGTH,
  strengthBand, streetCase, tidy, todayStatus, wktToGeometry,
  type AgentReply, type AskContext, type AskTurn, type Category, type Extent, type PennEvent, type View,
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
const TICKET_ICON = "penn-flag-ticketed";
// The icon is drawn at 2x for sharp edges; 22 x 27 px on the map. Its pole's foot sits 3 px in from the left.
const FLAG_PX = { w: 44, h: 54, ratio: 2, footX: 3 };
const isPointGeom = (e: PennEvent) => /^(MULTI)?POINT/.test(e.geometry_wkt);
// What a click or hover picks first where things overlap: a flag, then a dot, then lines by severity.
// A marker is an explicit target; the street line under it can still be clicked beside it.
const pickOrder = (e: PennEvent) => (isListing(category(e)) ? 0 : isPointGeom(e) ? 1 : 2 + LINE_RANK(e));
/** Records on one spot by date: in effect today first, then upcoming soonest first, then ones already
 *  over, most recent first. The furthest-away events end up last. */
const startMs = (e: PennEvent) => Date.parse(e.start_ts ?? `${e.start_date ?? "9999-12-31"}T00:00`);
function byWhen(a: PennEvent, b: PennEvent): number {
  const now = appNow().getTime();
  const band = (e: PennEvent) => (activeToday(e) ? 0 : startMs(e) > now ? 1 : 2);
  return band(a) - band(b) || (band(a) === 2 ? startMs(b) - startMs(a) : startMs(a) - startMs(b));
}
// Neighborhood outlines: a slimmed copy of data/areas/philadelphia-neighborhoods.geojson (OpenDataPhilly),
// the same boundaries the server uses when a question names a neighborhood.
const HOODS_URL = "/neighborhoods.geojson";
const HOOD_LAYERS = ["hood-line", "hood-focus", "hood-label"];
// Everything past the city line, built from the neighborhoods by scripts/build_city_mask.py.
const CITY_MASK_URL = "/city-mask.geojson";
const SIGN = "#1E5B44";   // --sign in globals.css
// The ask box's rolling hint: one of each kind of question PENN answers, alternating so no two neighbors are
// alike. A corner and a time; an event by name (past years from City notices, the next date, and the web when
// PENN has no record); a block party, a ticketed event or construction near a place; whether something closes
// every year; and a trip. The place questions resolve with /locate; the event ones by name (find_event).
const EXAMPLES = ["Spruce and 15th next week", "When is the Philadelphia Marathon, and what closes?",
  "Any block parties in Fishtown this weekend?", "Any concerts in Old City tonight?",
  "Is there construction in Francisville?", "Does the Mummers Parade close Broad Street every year?",
  "Can I drive to Rittenhouse on Sunday?", "Is the Rocky Run happening this year?"];
const EXAMPLE_MS = 3600;   // a little longer than before: some examples are whole questions now
const PRESETS = [{ days: 1, label: "24 hours" }, { days: 7, label: "7 days" }, { days: 30, label: "30 days" }];
// How long a window's city-wide records are reused before the map asks again: "the next 7 days"
// moves with the clock, and the daily load changes the data once a day.
const CITY_TTL_MS = 10 * 60 * 1000;
// same_as: [visitphilly id, ticketmaster id, reason] for one event listed by both (backend/penn/overlaps.py).
// covered: [block party permit id, event closure id] where the City's announced closure already closes every
// block of the permit that day: one closure, drawn once, as the announcement.
type CityLayer = { events: PennEvent[]; truncated?: boolean; same_as?: [string, string, string][]; covered?: [string, string][] };

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
// While an answer is open, the records it does not cite turn grey: not fainter, since fainter means "later" on
// this map, and an uncited closure in effect today must not read as one coming up.
const OUT_OF_ANSWER = { color: "#9AA3AE", opacity: 0.5, flag: 0.3 };
type Expr = maplibregl.ExpressionSpecification;
// The city layers an answer greys: the paint properties that fade, each one's own opacity, and its own color.
const FOCUS: { id: string; opacity: string[]; base: Expr; color?: { prop: string; value: string | Expr }; casing?: boolean }[] = [
  ...["parking", "sidewalk", "lane", "full"]
    .map(id => ({ id, opacity: ["line-opacity"], base: todayOpacity, color: { prop: "line-color", value: REASON_COLOR } })),
  { id: "full-casing", opacity: ["line-opacity"], base: todayOpacity, casing: true },
  ...["points", "points-minor"].map(id => ({ id, opacity: ["circle-opacity", "circle-stroke-opacity"], base: todayOpacity,
    color: { prop: "circle-color", value: REASON_COLOR } })),
  { id: "listings", opacity: ["icon-opacity"], base: flagOpacity },
];

/** The map's own window. Questions resolve their own dates on the server and never change it. */
function range(days: number): { start: string; end: string } {
  const now = appNow();
  return { start: now.toISOString(), end: new Date(now.getTime() + days * 864e5).toISOString() };
}

// The header's "last loaded" stamps: which ingest_runs sources feed each kind of record on the map.
const FRESH_GROUPS: { label: string; sources: string[] }[] = [
  { label: "permits", sources: ["lane_closures"] },
  { label: "event closures", sources: ["press_releases", "event_closures", "geocode_closures"] },
  { label: "listings", sources: ["visitphilly", "ticketmaster"] },
];

/** "today 9:07 AM", "yesterday 7:30 AM" or "Oct 14", in Philadelphia time, against the app's clock. */
function loadedStamp(iso: string, now: Date): string {
  const day = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const t = new Date(iso);
  const time = t.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  if (day(t) === day(now)) return `today ${time}`;
  if (day(t) === day(new Date(now.getTime() - 864e5))) return `yesterday ${time}`;
  return t.toLocaleDateString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" });
}

/** "Oct 17, 2026, 10:00 AM" in Philadelphia time. */
function demoStamp(iso: string): string {
  return new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric",
    year: "numeric", hour: "numeric", minute: "2-digit" });
}

/** One show at one venue on several dates: listings of the same kind with the same title at the same spot
 *  (4 decimal places, about 10 m). Fills `series` (each date's id -> the run, soonest first) and `leads` (the
 *  date drawn for the run: the next one not yet over, else the first). */
function groupSeries(events: PennEvent[], series: Map<string, PennEvent[]>, leads: Set<string>) {
  series.clear(); leads.clear();
  const runs = new Map<string, PennEvent[]>();
  for (const e of events) {
    if (!isListing(category(e)) || e.centroid_lat == null || e.centroid_lon == null) continue;
    const k = `${category(e)}|${tidy(e.title).toLowerCase()}|${e.centroid_lat.toFixed(4)}|${e.centroid_lon.toFixed(4)}`;
    runs.set(k, [...(runs.get(k) ?? []), e]);
  }
  const at = (e: PennEvent) => Date.parse(e.start_ts ?? `${e.start_date ?? "9999-12-31"}T00:00`);
  for (const run of runs.values()) {
    if (run.length < 2) continue;
    run.sort((a, b) => at(a) - at(b));
    run.forEach(e => series.set(e.event_id, run));
    leads.add((run.find(e => daysAway(e) >= 0) ?? run[0]).event_id);
  }
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
// From this width the answer is a column down the left (see .sheet in globals.css), not a sheet over the bottom.
const hasRail = () => typeof window !== "undefined" && window.matchMedia("(min-width: 900px)").matches;
const RAIL_W = 400;     // --rail-w
const TOPBAR_H = 50;    // --topbar-h
const KEY_W = 224;      // .legend width

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
  const [zoomedOut, setZoomedOut] = useState(false);   // below MINOR_ZOOM: the minor kinds are not drawn
  const [turn, setTurn] = useState(0);   // how many times the ask box's example has rolled
  const [mapLoading, setMapLoading] = useState(true);
  const [mapError, setMapError] = useState("");
  const [loaded, setLoaded] = useState<Record<string, string>>({});   // source -> last successful load (p4-04)

  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const sent = useRef<string[]>([]);   // questions already asked, oldest first; Up and Down walk them
  // The conversation PENN remembers: the last few exchanges and the place and window the last answer
  // used, so "what about next week?" knows where. "New topic" (or clearing the pin) forgets it.
  const turns = useRef<AskTurn[]>([]);
  const convo = useRef<AskContext | null>(null);
  const [onTopic, setOnTopic] = useState(false);
  const pickFor = useRef<string | null>(null);   // the question waiting for a point the user is now picking
  const sentAt = useRef(-1);           // index into sent while walking, -1 when typing fresh
  const [status, setStatus] = useState<Status>("idle");
  const [place, setPlace] = useState<Place | null>(null);
  const [reply, setReply] = useState<AgentReply | null>(null);
  const [sheetOpen, setSheetOpen] = useState(true);
  const [citedIds, setCitedIds] = useState<string[]>([]);
  const [pin, setPin] = useState<[number, number] | null>(null);   // [lon, lat]
  const [picking, setPicking] = useState(false);

  const [showHoods, setShowHoods] = useState(true);
  // Map key rows switched off: their records are left out of the drawn layer (and so out of hover and click).
  const [hiddenCats, setHiddenCats] = useState<ReadonlySet<Category>>(new Set());
  const hiddenRef = useRef(hiddenCats); hiddenRef.current = hiddenCats;
  const showHoodsRef = useRef(showHoods); showHoodsRef.current = showHoods;
  // The corner or address the latest question resolved to, marked on the map.
  const [placePoint, setPlacePoint] = useState<[number, number] | null>(null);
  const placePointRef = useRef(placePoint); placePointRef.current = placePoint;
  // Landmarks the answer names when the question named no place (p3-10): pinned and labeled, framed by the map.
  const [marks, setMarks] = useState<Mark[]>([]);
  const marksRef = useRef(marks); marksRef.current = marks;
  // While an answer that cites records is open, everything it does not cite steps back. Hiding the
  // answer brings the whole map back.
  const focus = status === "done" && sheetOpen && citedIds.length ? citedIds : null;
  const focusRef = useRef(focus); focusRef.current = focus;
  const focusKey = focus?.join(",") ?? "";
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
  // The empty ask box rolls through example questions; typing, a pin, or a phone's narrow box stops it.
  const rolling = !question && !pin && !narrow;
  useEffect(() => {
    if (!rolling) return;
    const t = setInterval(() => setTurn(n => n + 1), EXAMPLE_MS);
    return () => clearInterval(t);
  }, [rolling]);

  // Handlers registered once on the map read the latest state through refs.
  const winRef = useRef(win); winRef.current = win;
  const pickingRef = useRef(picking); pickingRef.current = picking;
  const panelOpenRef = useRef(panelOpen); panelOpenRef.current = panelOpen;
  const sheetUpRef = useRef(false); sheetUpRef.current = status !== "idle";

  /** Room the floating UI takes, so a fitted place lands in the visible part of the map. */
  const padding = useCallback((expectSheet = false): maplibregl.PaddingOptions => {
    const narrow = isNarrow();
    if (hasRail()) {
      // The answer is a column on the left and the key stands in the bottom right, so the place goes between.
      const panel = panelOpenRef.current ? 380 : 0;
      return { top: 12 + TOPBAR_H + 24, bottom: 24,
        left: expectSheet || sheetUpRef.current ? 12 + RAIL_W + 32 : 48,
        right: panel ? panel + 40 : 12 + KEY_W + 36 };
    }
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

  // The whole city's records for each time window, fetched once (about 2,600 records, 300 KB gzipped)
  // and kept for CITY_TTL_MS. Panning and zooming never go back to the server, so nothing pops in at the
  // edges; a window's request is shared while it is in flight.
  const cityCache = useRef(new Map<number, { at: number; req: Promise<CityLayer> }>());
  const cityEvents = useRef<PennEvent[]>([]);
  // Matched pairs, both ways: a Ticketmaster event's visitphilly pick, and a pick's Ticketmaster dates.
  const pickOf = useRef(new Map<string, string>());
  const salesOf = useRef(new Map<string, string[]>());
  // One show at one venue on several dates is one flag: every date's record maps to the whole run, and only
  // its lead (the next date still to come) is drawn and counted.
  const seriesOf = useRef(new Map<string, PennEvent[]>());
  const leads = useRef(new Set<string>());
  // Block party permits under an announced event closure: permit id -> the closure drawn for it.
  const coveredBy = useRef(new Map<string, string>());
  const folded = useCallback((e: PennEvent) => {
    if (seriesOf.current.has(e.event_id) && !leads.current.has(e.event_id)) return true;
    if (coveredBy.current.has(e.event_id) && !hiddenRef.current.has("event")) return true;
    return category(e) === "listing" && salesOf.current.has(e.event_id) && !hiddenRef.current.has("ticketed");
  }, []);

  /** What the map draws: switched-off categories left out, and a visitphilly pick that Ticketmaster also lists
   *  folded into the Ticketmaster flag (unless ticketed events are switched off, when the pick shows alone). */
  const drawn = useCallback(() => cityEvents.current.filter(e => !hiddenRef.current.has(category(e)) && !folded(e) && !isOver(e)), [folded]);

  /** The other listings of the same event, for the panel and the hover card. */
  const partnersOf = useCallback((e: PennEvent): PennEvent[] => {
    const ids = e.source === "ticketmaster" ? [pickOf.current.get(e.event_id)] : salesOf.current.get(e.event_id) ?? [];
    return ids.flatMap(id => (id && known.current.get(id)) || []);
  }, []);

  /** Put the city's records on the map, minus the categories switched off in the key. */
  const drawCity = useCallback(() => {
    const shown = drawn();
    (map.current?.getSource("events") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: toFeatures(shown) });
  }, [drawn]);

  const fetchCity = useCallback((days: number): Promise<CityLayer> => {
    const hit = cityCache.current.get(days);
    if (hit && Date.now() - hit.at < CITY_TTL_MS) return hit.req;
    const { start, end } = range(days);
    const req = getJSON<CityLayer>(`/map?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    cityCache.current.set(days, { at: Date.now(), req });
    req.catch(() => cityCache.current.delete(days));
    return req;
  }, []);

  /** The legend's "in view" counts: records whose centroid is on screen, as the server's box used to pick. */
  const countInView = useCallback(() => {
    const m = map.current;
    if (!m) return;
    const b = m.getBounds();
    const out = m.getZoom() < MINOR_ZOOM;
    setZoomedOut(out);
    // A pick folded into its Ticketmaster flag, or a show's later dates, are not counted again; switched-off
    // rows keep their counts. Zoomed out, the key counts only what is drawn: no minor lines.
    setEvents(cityEvents.current.filter(e => e.centroid_lon != null && e.centroid_lat != null && !folded(e) && !isOver(e)
      && !(out && isMinor(e)) && b.contains([e.centroid_lon, e.centroid_lat])));
  }, [folded]);

  const load = useCallback(() => {
    const m = map.current;
    if (!m || !m.getSource("events")) return;
    const seq = ++loadSeq.current;
    const days = winRef.current;
    setMapLoading(true);
    fetchCity(days)
      .then(({ events, truncated, same_as, covered }) => {
        if (seq !== loadSeq.current) return;
        events.forEach(e => known.current.set(e.event_id, e));
        cityEvents.current = events;
        pickOf.current = new Map((same_as ?? []).map(([pick, sale]) => [sale, pick]));
        salesOf.current = new Map();
        (same_as ?? []).forEach(([pick, sale]) => salesOf.current.set(pick, [...(salesOf.current.get(pick) ?? []), sale]));
        coveredBy.current = new Map(covered ?? []);
        groupSeries(events, seriesOf.current, leads.current);
        setMapError(truncated ? "The map is showing only part of the city's records for this window." : "");
        drawCity();
        countInView();
        // Warm the other windows so switching between them is instant.
        PRESETS.forEach(p => { if (p.days !== days) fetchCity(p.days).catch(() => {}); });
      })
      .catch(err => { if (seq === loadSeq.current) setMapError(`The map could not load permits: ${err.message}. Check that the PENN API is running.`); })
      .finally(() => { if (seq === loadSeq.current) setMapLoading(false); });
  }, [fetchCity, countInView, drawCity]);

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
    // No attribution control: the map credits are in the fine print along the page's bottom edge.

    const init = () => {
      if (m.getSource("events")) return;
      const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
      m.addSource("events", { type: "geojson", data: empty, promoteId: "event_id" });
      m.addSource("cited", { type: "geojson", data: empty });
      m.addSource("selected", { type: "geojson", data: empty });
      const flag = new Image(FLAG_PX.w, FLAG_PX.h);
      flag.onload = () => { if (!m.hasImage(FLAG_ICON)) m.addImage(FLAG_ICON, flag, { pixelRatio: FLAG_PX.ratio }); };
      flag.src = flagDataUrl(FLAG_PX.w, FLAG_PX.h);
      const ticket = new Image(FLAG_PX.w, FLAG_PX.h);
      ticket.onload = () => { if (!m.hasImage(TICKET_ICON)) m.addImage(TICKET_ICON, ticket, { pixelRatio: FLAG_PX.ratio }); };
      ticket.src = flagDataUrl(FLAG_PX.w, FLAG_PX.h, "ticketed");
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
      // A white street on Positron's near-white ground shows only where buildings frame it; in a low-density
      // neighborhood like Cedarbrook, with few buildings mapped, the streets vanish. A light grey edge under every
      // street keeps the grid readable at every zoom.
      const firstStreet = ["highway_minor", "highway_major_casing"].find(id => m.getLayer(id));
      ["highway_minor", "highway_major_inner"].forEach(id => {
        const spec = m.getStyle().layers.find(l => l.id === id) as maplibregl.LineLayerSpecification | undefined;
        if (!spec) return;
        m.addLayer({ ...spec, id: `${id}-edge`, paint: { "line-color": ROAD.streetEdge,
          "line-width": ["interpolate", ["exponential", 1.55], ["zoom"], 11, 2.4, 13, 3.4, 20, 22] } }, firstStreet);
      });
      // Rail in a tunnel (the Center City Commuter Connection under Filbert and Market) is not on the street and
      // drew a hatched line through the blocks closures need. Surface rail stays: no one drives across it. Positron
      // already leaves out subway tunnels (its "transit" class).
      ["railway", "railway_dashline", "railway_service", "railway_service_dashline"].forEach(id => {
        const f = m.getLayer(id) && m.getFilter(id);
        if (f) m.setFilter(id, ["all", f, ["!=", ["get", "brunnel"], "tunnel"]] as maplibregl.FilterSpecification);
      });
      // Parks, squares, fields and woods olive inside the city only. Positron draws parks first, right over its background; an
      // olive copy of its woods goes there too (the original, drawn later, is hidden), then the city mask: the
      // background color everywhere past the city line, over the olive and under water, roads and labels.
      if (m.getLayer("park")) m.setPaintProperty("park", "fill-color", PARK.park);
      const wood = m.getStyle().layers.find(l => l.id === "landcover_wood") as maplibregl.FillLayerSpecification | undefined;
      const firstAbovePark = m.getLayer("water") ? "water" : undefined;
      if (wood) {
        m.addLayer({ ...wood, id: "wood-city", paint: { ...wood.paint, "fill-color": PARK.wood } }, firstAbovePark);
        m.setLayoutProperty("landcover_wood", "visibility", "none");
      }
      // Green spaces Positron never draws, in the same olive as parks: squares and lawns are grass landcover
      // (Washington Square is grass/park there, not in the park layer), and ballfields, playgrounds and running
      // tracks are landuse.
      const tiles = wood?.source ?? "openmaptiles";
      m.addLayer({ id: "grass-city", type: "fill", source: tiles, "source-layer": "landcover",
        filter: ["==", ["get", "class"], "grass"], paint: { "fill-color": PARK.park } }, firstAbovePark);
      m.addLayer({ id: "fields-city", type: "fill", source: tiles, "source-layer": "landuse",
        filter: ["in", ["get", "class"], ["literal", ["pitch", "playground", "track"]]], paint: { "fill-color": PARK.park } }, firstAbovePark);
      m.addSource("city-mask", { type: "geojson", data: CITY_MASK_URL });
      m.addLayer({ id: "city-mask", type: "fill", source: "city-mask",
        paint: { "fill-color": (m.getLayer("background") && m.getPaintProperty("background", "background-color") as string) || "rgb(242,243,240)" } },
        firstAbovePark);

      // Neighborhoods under everything else of ours: a soft slate band (no dashes, so it never reads as a permit),
      // plainest when zoomed out, where neighborhoods are the useful frame. At street level it stays, narrower than
      // the street it runs down, so the street still shows on either side. The asked-about one is a solid
      // sign-green line at every zoom.
      m.addSource("hoods", { type: "geojson", data: HOODS_URL });
      add({ id: "hood-line", type: "line", source: "hoods",
        paint: { "line-color": HOOD.line, "line-blur": 1,
          "line-width": ["interpolate", ["linear"], ["zoom"], 11, 3, 14, 4.5, 17, 5],
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 11, 0.4, 13, 0.3, 15, 0.24] } });
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
      // The corner or address asked about: a sign-green target over the permits, ringed in white so it
      // reads on any line color. A neighborhood gets its outline (hood-focus) instead.
      m.addSource("place", { type: "geojson", data: empty });
      add({ id: "place-ring", type: "circle", source: "place",
        paint: { "circle-color": "rgba(255,255,255,0.55)", "circle-radius": zoomWidth(10, 16),
          "circle-stroke-color": SIGN, "circle-stroke-width": 3 } });
      add({ id: "place-dot", type: "circle", source: "place",
        paint: { "circle-color": SIGN, "circle-radius": zoomWidth(3.5, 5), "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
      m.addSource("marks", { type: "geojson", data: empty });
      m.addLayer({ id: "mark-dot", type: "circle", source: "marks",
        paint: { "circle-color": SIGN, "circle-radius": zoomWidth(4, 6), "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      m.addLayer({ id: "mark-label", type: "symbol", source: "marks",
        layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Bold"], "text-size": 12, "text-anchor": "top",
          "text-offset": [0, 0.8], "text-allow-overlap": true, "text-ignore-placement": true },
        paint: { "text-color": SIGN, "text-halo-color": "#fff", "text-halo-width": 1.6 } });
      // Below the basemap's labels, so street names win where they collide. The glyph server has one family (Noto
      // Sans), so areas are told apart from streets the way maps usually do it: spaced bold capitals, level, for a
      // neighborhood; regular title case along the line, in grey, for a street.
      add({ id: "hood-label", type: "symbol", source: "hoods", minzoom: 12,
        layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Bold"], "text-transform": "uppercase",
          "text-size": zoomWidth(10, 12.5), "text-max-width": 9, "text-letter-spacing": 0.16, "text-line-height": 1.3 },
        paint: { "text-color": HOOD.label, "text-halo-color": "rgba(255,255,255,0.9)", "text-halo-width": 1.5 } });

      // Listings (visitphilly): flags over everything, street names included, so a flag on a closed street
      // stays visible and clickable. Under each flag's foot, a highlighter dot when cited and a ring when open.
      m.addLayer({ id: "cited-point", type: "circle", source: "cited", filter: isPoint,
        paint: { "circle-color": HIGHLIGHT, "circle-radius": zoomWidth(9, 14), "circle-opacity": 0.85, "circle-blur": 0.2 } });
      m.addLayer({ id: "selected-point", type: "circle", source: "selected", filter: isPoint,
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": zoomWidth(8, 12), "circle-stroke-color": "#1B2433", "circle-stroke-width": 2.5 } });
      m.addLayer({ id: "listings", type: "symbol", source: "events", filter: ["in", ["get", "cat"], ["literal", ["listing", "ticketed"]]],
        layout: { "icon-image": ["match", ["get", "cat"], "ticketed", TICKET_ICON, FLAG_ICON], "icon-anchor": "bottom-left", "icon-offset": [-FLAG_PX.footX, 0],
          "icon-allow-overlap": true, "icon-ignore-placement": true, "symbol-sort-key": ["-", 0, ["get", "days"]] },   // today's on top
        paint: { "icon-opacity": flagOpacity } });
      showHoodsOn(m, showHoodsRef.current);
      focusOn(m, focusRef.current);
      markPlace(m, placePointRef.current);
      markLandmarks(m, marksRef.current);
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
      }).sort((a, b) => pickOrder(a) - pickOrder(b) || byWhen(a, b));
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
    m.on("moveend", () => { clearTimeout(t); t = setTimeout(countInView, 150); });
    return () => { clearTimeout(t); m.remove(); map.current = null; };   // StrictMode mounts twice in development
  }, [load, countInView, select, closePanel]);

  useEffect(() => { load(); }, [win, load]);

  useEffect(() => {
    getJSON<{ source: string; last_ok: string }[]>("/freshness")
      .then(rows => setLoaded(Object.fromEntries(rows.map(r => [r.source, r.last_ok]))))
      .catch(() => {});   // the stamps are a nicety; the map's own error covers a missing API
  }, []);

  useEffect(() => { drawCity(); countInView(); }, [hiddenCats, drawCity, countInView]);

  // A closure leaves the map when its own end time passes, even with the page left open: redraw each minute.
  useEffect(() => {
    const t = setInterval(() => { drawCity(); countInView(); }, 60_000);
    return () => clearInterval(t);
  }, [drawCity, countInView]);

  useEffect(() => { if (map.current?.getLayer("hood-line")) showHoodsOn(map.current, showHoods); }, [showHoods]);

  useEffect(() => { if (map.current?.getLayer("full")) focusOn(map.current, focusRef.current); }, [focusKey]);

  useEffect(() => { if (map.current?.getSource("place")) markPlace(map.current, placePoint); }, [placePoint]);
  useEffect(() => { if (map.current?.getSource("marks")) markLandmarks(map.current, marks); }, [marks]);

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
  async function showCited(ids: string[], fitIfNoPlace: boolean, extra: maplibregl.LngLatBounds | null = null) {
    const m = map.current;
    const got = await Promise.all(ids.slice(0, MAX_CITED_ON_MAP).map(id =>
      known.current.get(id) ?? getJSON<PennEvent>(`/events/${encodeURIComponent(id)}`).catch(() => null)));
    const evs = got.filter((e): e is PennEvent => !!e);
    evs.forEach(e => known.current.set(e.event_id, e));
    const features = toFeatures(evs).map(f => ({ ...f, properties: { ...f.properties, color: COLOR[f.properties!.cat as Category] } }));
    (m?.getSource("cited") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
    let b = fitIfNoPlace ? boundsOf(features) : null;
    if (fitIfNoPlace && extra) b = b ? b.extend(extra) : extra;   // the landmarks the answer names, too
    if (b) fly(b, 16.5);
  }

  async function ask(q: string, at: [number, number] | null = pin) {
    q = q.trim();
    if (!q) return;
    const seq = ++askSeq.current;
    const stale = () => seq !== askSeq.current;
    setAsked(q); setStatus("locating"); setReply(null); setSheetOpen(true); setCitedIds([]); setPlace(null); setPlacePoint(null); setMarks([]);
    (map.current?.getSource("cited") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: [] });

    // The place resolves in milliseconds without the model: move the map while the answer is written.
    const memory = { history: turns.current.slice(-3), context: convo.current };
    const agent = askAgent(at ? { question: q, lon: at[0], lat: at[1], ...memory } : { question: q, ...memory });
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
        if (l.found && l.view) {
          flew = true; placeView = l.view; fly(l.view.bounds, 17, true); setPlace({ name: l.place, window: l.window });
          setPlacePoint(l.view.point ?? null);
        }
        setStatus(s => (s === "locating" ? "reading" : s));
      }).catch(() => { if (!stale()) setStatus(s => (s === "locating" ? "reading" : s)); });
    }

    try {
      const r = await agent;
      if (stale()) return;
      setReply(r);
      setStatus("done");
      turns.current = [...turns.current, { question: q, answer: r.answer ?? r.message ?? "" }].slice(-3);
      if (r.context) convo.current = r.context;
      setOnTopic(true);
      if (r.outside) return;
      if (r.place || r.window) setPlace(p => ({ name: r.place ?? p?.name ?? null, window: r.window ?? p?.window }));
      const view: View | null | undefined = r.view;
      if (!flew && view) { flew = true; fly(view.bounds, 17, true); }
      if (!at && view?.point) setPlacePoint(view.point);   // a picked point already has its pin
      // Settle once the answer has its real height, so the place sits clear of the sheet.
      const settle = placeView ?? view;
      if (settle) requestAnimationFrame(() => requestAnimationFrame(() => { if (!stale()) fly(settle.bounds); }));
      const ids = r.answer ? Array.from(new Set([...citationOrder(r.answer), ...(r.cited ?? [])])) : r.cited ?? [];
      setCitedIds(ids);
      // No place in the question: the landmarks the answer names are pinned, and the map frames them with
      // the cited records (one landmark: centered; several: zoomed out to fit all).
      const lms = !flew ? r.landmarks ?? [] : [];
      setMarks(lms.map(l => ({ name: l.name, point: l.point })));
      const lmBounds = lms.reduce<maplibregl.LngLatBounds | null>((acc, l) => {
        const lb = new maplibregl.LngLatBounds(l.bounds[0], l.bounds[1]);
        return acc ? acc.extend(lb) : lb;
      }, null);
      if (ids.length) await showCited(ids, !flew, lmBounds);
      else if (lmBounds) fly(lmBounds, 16);
    } catch (err) {
      if (stale()) return;
      setStatus("error");
      setReply({ message: `PENN could not answer: ${(err as Error).message}. Check that the PENN API is running, then ask again.` });
      setQuestion(cur => cur || q);   // give the question back so asking again is one click
    }
  }

  // The box empties once a question is sent; the question stays above its answer in the sheet.
  function submit() {
    const q = question.trim();
    if (!q) return;
    if (sent.current[sent.current.length - 1] !== q) sent.current.push(q);
    sentAt.current = -1;
    setQuestion("");
    ask(q);
  }

  // Up brings back earlier questions to edit or resend, Down walks forward again, as in a terminal.
  function recall(e: ReactKeyboardEvent<HTMLInputElement>) {
    const list = sent.current;
    if (!list.length || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    if (sentAt.current === -1 && (e.key === "ArrowDown" || question)) return;   // never overwrite a draft
    e.preventDefault();
    const at = e.key === "ArrowUp"
      ? (sentAt.current === -1 ? list.length - 1 : Math.max(0, sentAt.current - 1))
      : sentAt.current + 1;
    sentAt.current = at >= list.length ? -1 : at;
    setQuestion(sentAt.current === -1 ? "" : list[sentAt.current]);
  }

  // A pin picked from "Pick a point on the map" under PENN's question answers the waiting question.
  useEffect(() => {
    if (pin && pickFor.current) { const q = pickFor.current; pickFor.current = null; ask(q, pin); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pin]);

  // Start fresh: PENN forgets the earlier questions and the place and window they used.
  function forget() {
    turns.current = []; convo.current = null; pickFor.current = null;
    setOnTopic(false);
  }

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
        <FreshLine loaded={loaded} />
      </header>

      {mapError && <div className="map-error" role="alert">{mapError}</div>}
      {picking && <div className="pick-hint" role="status">Click the map where you mean. Esc cancels.</div>}

      {hover && (
        <div className="hover-tip" style={{ left: hover.x + 14, top: hover.y + 14 }} aria-hidden="true">
          <TipBody e={hover.e} partners={partnersOf(hover.e)} dates={seriesOf.current.get(hover.e.event_id)?.length ?? 1} />
        </div>
      )}

      <PermitPanel open={panelOpen} choices={choices} event={selected} loading={detailLoading} error={detailError}
        citedNumber={citedNumber} partnersOf={partnersOf} datesOf={e => seriesOf.current.get(e.event_id) ?? [e]} onPick={e => select(e)} onBack={choices.length > 1 ? () => setSelected(null) : null}
        onClose={closePanel} />

      <div className="dock">
        <div className="dock-row" ref={dockRef}>
          {hasSheet && (
            <section className={`sheet${sheetOpen ? "" : " collapsed"}`} aria-live="polite" aria-busy={busy}>
              <div className="sheet-head">
                {place?.name ? <span className="plate">{place.name}</span>
                  : busy ? <span className="plate pending">Finding the place</span> : null}
                <span className="sheet-when">{reply?.window ?? place?.window ?? ""}</span>
                {onTopic && !busy && (
                  <button type="button" className="text-btn" title="PENN forgets the earlier questions and their place"
                    onClick={forget}>New topic</button>
                )}
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
                  {reply?.message && <p className={reply.outside || status === "error" ? "sheet-error" : "sheet-msg"}>{reply.message}</p>}
                  {reply?.answer && (
                    <>
                      {kindNote && <p className="kind-note">{kindNote}</p>}
                      <AnswerText text={reply.answer} titles={titles} selected={selected?.event_id ?? null} onCite={onCite} web={reply.web} />
                      {order.length > 0 && <p className="sheet-foot">Numbers open the permit. Highlighted streets on the map are
                        the ones cited; other records turn grey until you hide the answer.</p>}
                      {reply.asked_back && !pin && (
                        <button type="button" className="text-btn" onClick={() => { pickFor.current = asked; setPicking(true); }}>
                          Pick a point on the map</button>
                      )}
                    </>
                  )}
                </div>
              )}
            </section>
          )}
          <Legend counts={counts} bands={bands} citedCount={citedIds.length} hoods={showHoods} onHoods={setShowHoods}
            hidden={hiddenCats} onHidden={setHiddenCats} zoomedOut={zoomedOut}
            loading={mapLoading && !events.length} />
        </div>

        <form className="askbar" onSubmit={e => { e.preventDefault(); if (!busy) submit(); }}>
          <label htmlFor="q" className="sr-only">Ask PENN about a street or corner</label>
          {pin ? (
            <span className="pin-chip">
              Pinned point
              <button type="button" aria-label="Clear the pinned point" onClick={() => { setPin(null); forget(); }}>
                <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 2l6 6m0-6L2 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
              </button>
            </span>
          ) : (
            <button type="button" className={`pick-btn${picking ? " on" : ""}`} aria-pressed={picking}
              title="Pick a point on the map" aria-label="Pick a point on the map" onClick={() => setPicking(p => !p)}>
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 16s5-4.6 5-8.6A5 5 0 0 0 4 7.4C4 11.4 9 16 9 16z" fill="none" stroke="currentColor" strokeWidth="1.6" /><circle cx="9" cy="7.4" r="1.8" fill="currentColor" /></svg>
              <span className="pick-label" aria-hidden="true">{picking ? "Click the map" : "Pick on map"}</span>
            </button>
          )}
          <div className="ask-field">
            <input id="q" ref={inputRef} value={question} onChange={e => { sentAt.current = -1; setQuestion(e.target.value); }}
              onKeyDown={recall} autoComplete="off"
              placeholder={pin ? "Ask about the pinned point" : narrow ? "Ask about streets, events or closures" : ""} />
            {/* The empty box's hint, with its example rolling up to the next every few seconds: a placeholder
                attribute can't animate, so this sits over the input and lets clicks through. */}
            {rolling && (
              <span className="ask-hint" aria-hidden="true">
                Ask about streets, events or closures, like{" "}
                <span className="roll">
                  <span key={turn} className={turn ? "roll-in" : undefined}>{EXAMPLES[turn % EXAMPLES.length]}</span>
                  {turn > 0 && <span key={`out-${turn}`} className="roll-out">{EXAMPLES[(turn - 1) % EXAMPLES.length]}</span>}
                </span>
              </span>
            )}
          </div>
          <button type="submit" className="ask-btn" disabled={!question.trim() || busy}>{busy ? "Asking" : "Ask"}</button>
        </form>
        {/* Two rows: what the data is and where it comes from, then privacy and the map credits. */}
        <div className="fine-print">
          <p className="fine-row">
            <span className="fine-give" title={"Not an official City of Philadelphia source. Data is shown as published "
              + "and may be incomplete or out of date; check the linked record. Sources: City of Philadelphia street "
              + "permits and event-closure notices, race organizers, visitphilly.com and Ticketmaster listings, and "
              + "web pages found with Tavily search when PENN has no record."}>
              {PINNED_NOW && <strong className="fine-demo">Demo snapshot: data as of {demoStamp(PINNED_NOW)}. </strong>}
              Not an official City of Philadelphia source. Data may be incomplete or out of date; check the linked
              record. Sources: <a href="https://opendataphilly.org/datasets/street-lane-closures/"
              target="_blank" rel="noopener">City of Philadelphia</a> permits and closure notices, race
              organizers, <a href="https://www.visitphilly.com/" target="_blank" rel="noopener">visitphilly.com</a>,{" "}
              <a href="https://www.ticketmaster.com/" target="_blank" rel="noopener">Ticketmaster</a>, web pages
              found with <a href="https://www.tavily.com/" target="_blank" rel="noopener">Tavily</a>.
            </span>
          </p>
          <p className="fine-row">
            {/* Tavily may keep and use the search terms it receives (its terms 6.5, 9.2); penn/web/tavily.py
                sends only a short query or an event name and year, never the question itself. */}
            <span className="fine-give fine-privacy" title={"PENN sets no cookies and shows no ads. Questions you ask are sent "
              + "to NVIDIA Nemotron on Nebius to answer them. When PENN has no record, a short web search (an event's name "
              + "and year, or a few words on what you asked, never your question itself) is sent to Tavily, which may keep it."}>
              Privacy: no cookies, no ads; questions go to NVIDIA Nemotron on Nebius, short web searches to Tavily.
            </span>
            {/* Map credits. OpenStreetMap asks for visible credit; the neighborhood outlines are CC BY 4.0,
                which requires naming their author. */}
            <span className="fine-credits">
              <a href="https://openfreemap.org/" target="_blank" rel="noopener">OpenFreeMap</a>{" "}
              <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">© OpenMapTiles</a>{" "}
              <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors</a>
              {" · "}Neighborhoods © <a href="https://opendataphilly.org/datasets/philadelphia-neighborhoods/" target="_blank"
              rel="noopener">Robert Cheetham</a>, <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank"
              rel="noopener">CC BY 4.0</a>
            </span>
          </p>
        </div>
      </div>
    </main>
  );
}

/** Our neighborhood outlines replace Positron's OpenStreetMap neighborhood names, which use other boundaries. */
function showHoodsOn(m: maplibregl.Map, on: boolean) {
  HOOD_LAYERS.forEach(id => m.getLayer(id) && m.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  if (m.getLayer("label_other")) m.setLayoutProperty("label_other", "visibility", on ? "none" : "visible");
}

/** Fade every record not in cited; null restores each layer's own opacity. */
function focusOn(m: maplibregl.Map, cited: string[] | null) {
  const keep = cited?.length ? ["in", ["get", "event_id"], ["literal", cited]] : null;
  FOCUS.forEach(({ id, opacity, base, color, casing }) => {
    if (!m.getLayer(id)) return;
    // A white casing under a grey line would read as a road, so it goes; flags can't turn grey, so they recede.
    const out = casing ? 0 : id === "listings" ? OUT_OF_ANSWER.flag : OUT_OF_ANSWER.opacity;
    const o = (keep ? ["case", keep, base, out] : base) as Expr;
    opacity.forEach(p => m.setPaintProperty(id, p, o));
    if (color) m.setPaintProperty(id, color.prop, keep ? ["case", keep, color.value, OUT_OF_ANSWER.color] : color.value);
  });
}

type Mark = { name: string; point: [number, number] };

function markLandmarks(m: maplibregl.Map, list: Mark[]) {
  (m.getSource("marks") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection",
    features: list.map(l => ({ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: l.point }, properties: { name: l.name } })) });
}

function markPlace(m: maplibregl.Map, p: [number, number] | null) {
  (m.getSource("place") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection",
    features: p ? [{ type: "Feature", geometry: { type: "Point", coordinates: p }, properties: {} }] : [] });
}

const MAX_TIP_PLACES = 2;

/** The hover card: category, a short title, the first blocks, and when. Long permits list dozens of blocks. */
// How much of the street a construction permit takes, after its reason on the hover card. A block party and an
// event closure close the street; no parking is a curb rule.
const HOW_MUCH: Record<Extent, string> = { full: "full street closed", lane: "one lane closed", sidewalk: "sidewalk only" };

function TipBody({ e, partners, dates }: { e: PennEvent; partners: PennEvent[]; dates: number }) {
  const cat = category(e);
  if (isListing(cat)) return <ListingTip e={e} partners={partners} dates={dates} />;
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
function ListingTip({ e, partners, dates }: { e: PennEvent; partners: PennEvent[]; dates: number }) {
  const { where, note } = listingWhere(e);
  const title = tidy(e.title);
  return (
    <>
      <span className={`tip-cat cat-${category(e)}`}>{LABEL[category(e)]}<span className="tip-src"> · {[e, ...partners.slice(0, 1)].map(x => LISTED_BY[x.source] ?? x.source).join(" and ")}</span></span>
      <span className="tip-title">{title.length > 70 ? `${title.slice(0, 68).trimEnd()}…` : title}</span>
      <dl className="tip-rows">
        <dt>When</dt><dd>{shortWhen(e)}{dates > 1 && <span className="tip-note">and {dates - 1} more date{dates > 2 ? "s" : ""}</span>}{note && <span className="tip-note">{note}</span>}</dd>
        {where && <><dt>Where</dt><dd>{where}</dd></>}
      </dl>
      <span className="tip-foot">
        <span className="tip-when">{todayStatus(e)}</span>
        <span className="tip-hint">Click for details</span>
      </span>
    </>
  );
}

/** "Updated yesterday 10:08 AM": the stalest of the three kinds' latest loads, so it never overstates how fresh
 *  the map is. Each kind's own stamp ("permits today 7:30 AM · ...") is in the hover title. */
function FreshLine({ loaded }: { loaded: Record<string, string> }) {
  const now = appNow();
  const lasts = FRESH_GROUPS.flatMap(g => {
    const last = g.sources.map(s => loaded[s]).filter(Boolean).sort((a, b) => Date.parse(a) - Date.parse(b)).pop();
    return last ? [{ label: g.label, last }] : [];
  });
  if (!lasts.length) return null;
  const stalest = lasts.reduce((a, b) => (Date.parse(a.last) <= Date.parse(b.last) ? a : b));
  return (
    <p className="fresh" title={`Last loaded: ${lasts.map(l => `${l.label} ${loadedStamp(l.last, now)}`).join(" · ")}`}>
      Updated {loadedStamp(stalest.last, now)}
    </p>
  );
}

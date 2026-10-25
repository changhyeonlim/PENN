"use client";

import * as maplibregl from "maplibre-gl";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { AnswerText, citationOrder } from "@/components/AnswerText";
import { GuardLine } from "@/components/GuardLine";
import { glow, usePresence, useSmoothSize } from "@/lib/motion";
import { Legend } from "@/components/Legend";
import { PermitPanel } from "@/components/PermitPanel";
import { ReplayBar } from "@/components/ReplayBar";
import { RoleGate } from "@/components/RoleGate";
import {
  activeToday, answerHeadline, appNow, askAgent, category, extent, GROUND, HOOD, isMinor, isOver, MINOR_ZOOM, PARK, isListing, COLOR, daysAway, pinDataUrl, getEvents, getJSON, HIGHLIGHT, LABEL, LISTED_BY, listingWhere, locate, PINNED_NOW, realNow, ROAD, setReplayClock, shortWhen, STRENGTH,
  strengthBand, streetCase, tidy, todayStatus, wktToGeometry, forgetRole, savedRole,
  type AgentReply, type AskContext, type AskTurn, type Busiest, type Category, type Extent, type PennEvent, type Role, type View,
} from "@/lib/api";

// MapLibre 6 is ESM-only and cannot find its worker inside a bundle: this exact `new URL(...)` form is what
// Next.js (Turbopack and webpack) recognizes and emits as a hashed asset.
maplibregl.setWorkerUrl(new URL("maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).toString());

// OpenFreeMap Positron: a quiet vector basemap with street names and no house numbers or shop labels.
const STYLE = "https://tiles.openfreemap.org/styles/positron";
// Center City, river to river and Spring Garden to South St.
const CENTER_CITY: [[number, number], [number, number]] = [[-75.1830, 39.9415], [-75.1415, 39.9620]];
// The answer's records lit on the map: its citations, then what the place search found there, biggest first.
// Enough for an event's whole latest route (a Marathon edition is ~60 blocks) beside the notices the answer cites.
const MAX_CITED_ON_MAP = 80;
// Hover and click look at the city's layers and at every layer an answer's records are drawn on: the late records
// (cite-late-line) and sidewalk ones (cited-walk) are not on cited-line.
const DATA_LAYERS = ["listings", "full", "lane", "sidewalk", "parking", "points", "points-minor", "cited-line", "cited-walk",
  "cite-late-line", "cited-point"];
// Lines by how much of the street they take, heaviest first; a no-parking curb line last.
const LINE_RANK = (e: PennEvent) => (category(e) === "parking" ? 3 : { full: 0, lane: 1, sidewalk: 2 }[extent(e)]);
const PIN_ICON = "penn-pin";
const TICKET_ICON = "penn-pin-ticketed";
// The icon is drawn at 2x for sharp edges; 21 x 27 px on the map (the key's 16 x 21, 30% larger, to find and tap).
// Its tip sits 1.8 px above the bottom edge (the white edge).
const PIN_PX = { w: 42, h: 54, ratio: 2, tipY: 1.8 };
const isPointGeom = (e: PennEvent) => /^(MULTI)?POINT/.test(e.geometry_wkt);
// What a click or hover picks first where things overlap: a pin, then a dot, then lines by severity.
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
// Lines by name, not "not a point": a line layer given an event listed at several places (a MultiPoint) draws its
// stops joined into one long line.
const isLine = ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString"]]] as maplibregl.FilterSpecification;
// An answer's lines by how much of the street they take, as the city's layers draw them (full, lane, sidewalk).
const isWalk = ["==", ["get", "ext"], "sidewalk"] as maplibregl.ExpressionSpecification;
const notWalk = ["!=", ["get", "ext"], "sidewalk"] as maplibregl.ExpressionSpecification;
// Zoom outermost (MapLibre allows ["zoom"] only there), the extent inside: a full closure's width, else one lane's.
const byExtent = (full: [number, number], lane: [number, number]) => ["interpolate", ["linear"], ["zoom"],
  12, ["match", ["get", "ext"], "full", full[0], lane[0]], 17, ["match", ["get", "ext"], "full", full[1], lane[1]]] as maplibregl.ExpressionSpecification;
const CITED_WIDTH = byExtent([3.5, 9], [1.8, 4.5]);
const CITED_EDGE = byExtent([5.5, 13], [3.4, 7]);
const isPoint = ["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]] as maplibregl.FilterSpecification;
const ofCat = (c: Category) => ["all", isLine, ["==", ["get", "cat"], c]] as maplibregl.FilterSpecification;
// A closure line of any reason at one weight (no-parking curb lines have their own layer).
const ofExtent = (x: Extent) => ["all", isLine, ["!=", ["get", "cat"], "parking"], ["==", ["get", "ext"], x]] as maplibregl.FilterSpecification;
// Full strength in effect today, then one step fainter per band of days away (STRENGTH in lib/api.ts).
const todayOpacity = ["step", ["get", "days"], STRENGTH[0].opacity,
  ...STRENGTH.slice(1).flatMap(s => [s.from, s.opacity])] as unknown as maplibregl.ExpressionSpecification;
const pinOpacity = ["max", 0.45, todayOpacity] as maplibregl.ExpressionSpecification;
// Color says why: construction, an event or a block party (or a no-parking curb).
const REASON_COLOR = ["match", ["get", "cat"], "event", COLOR.event, "blockparty", COLOR.blockparty,
  "parking", COLOR.parking, COLOR.construction] as maplibregl.ExpressionSpecification;
// While an answer is open, the records it does not cite turn grey: not fainter, since fainter means "later" on
// this map, and an uncited closure in effect today must not read as one coming up.
const OUT_OF_ANSWER = { color: "#9AA3AE", opacity: 0.5, pin: 0.3 };
type Expr = maplibregl.ExpressionSpecification;
// The city layers an answer greys: the paint properties that fade, each one's own opacity, and its own color.
type PaintProp = Parameters<maplibregl.Map["setPaintProperty"]>[1];   // MapLibre 6 types paint names
type Focus = { id: string; opacity: PaintProp[]; base: Expr; color?: { prop: PaintProp; value: string | Expr }; casing?: boolean };
const FOCUS: Focus[] = [
  ...["parking", "sidewalk", "lane", "full"]
    .map((id): Focus => ({ id, opacity: ["line-opacity"], base: todayOpacity, color: { prop: "line-color", value: REASON_COLOR } })),
  { id: "full-casing", opacity: ["line-opacity"], base: todayOpacity, casing: true },
  ...["points", "points-minor"].map((id): Focus => ({ id, opacity: ["circle-opacity", "circle-stroke-opacity"], base: todayOpacity,
    color: { prop: "circle-color", value: REASON_COLOR } })),
  { id: "listings", opacity: ["icon-opacity"], base: pinOpacity },
];

/** A preset window: from now, so many days ahead. */
function range(days: number): { start: string; end: string } {
  const now = realNow();
  return { start: now.toISOString(), end: new Date(now.getTime() + days * 864e5).toISOString() };
}

// Replay: the last 30 days of the city's records on a time slider, through today, opening on their busiest day
// (/busiest). Never later than today: permits not yet filed would leave a future day incomplete.
const REPLAY_DAYS = 30;
const REPLAY_AHEAD_DAYS = 7;   // records up to a week ahead of the slider fade in, as in the "7 days" view
// One step of the slider: a day. 12 or 8 hours also work (the step must divide a day); the map then shows what
// is in effect at any time in that step.
const REPLAY_STEP_HOURS = 24;
const REPLAY_STEP = REPLAY_STEP_HOURS * 3600e3;
const REPLAY_TICK_MS = 700;    // play: one step on the map every this long
const BUSY_CATS: ReadonlySet<Category> = new Set(["event", "blockparty", "listing", "ticketed"]);

/** Midnight in Philadelphia on a YYYY-MM-DD, as epoch ms (the offset differs in winter). */
function phillyMidnight(day: string): number {
  const utc = Date.parse(`${day}T00:00:00Z`);
  const name = new Date(utc + 12 * 3600e3).toLocaleString("en-US", { timeZone: "America/New_York", timeZoneName: "shortOffset" });
  return utc - Number(/GMT([+-]\d+)/.exec(name)?.[1] ?? -5) * 3600e3;
}

/** A YYYY-MM-DD so many days later (or earlier). */
const addDays = (day: string, n: number) => new Date(Date.parse(`${day}T00:00:00Z`) + n * 864e5).toISOString().slice(0, 10);

/** Whole Philadelphia days from 30 days ago through today (the pinned demo day in a snapshot): the slider's last
 *  step is today, and `endDay` (tomorrow) is exclusive. */
function replaySpan(): { start: number; end: number; startDay: string; endDay: string } {
  const today = realNow().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const startDay = addDays(today, -REPLAY_DAYS), endDay = addDays(today, 1);
  return { start: phillyMidnight(startDay), end: phillyMidnight(endDay), startDay, endDay };
}

/** Listings, shows, event closures and block parties in effect at any time in the step starting at `at`. */
function busyDuring(events: PennEvent[], at: number): number {
  const from = new Date(at), until = new Date(at + REPLAY_STEP);
  return events.filter(e => BUSY_CATS.has(category(e)) && activeToday(e, from, until)).length;
}

/** The step of a day with the most of them in effect (the first, if tied), where the replay opens. With day
 *  steps it is the day itself. */
function peakStep(events: PennEvent[], day: string): number {
  const midnight = phillyMidnight(day);
  let best = midnight, most = -1;
  for (let at = midnight; at < midnight + 24 * 3600e3; at += REPLAY_STEP) {
    const n = busyDuring(events, at);
    if (n > most) { most = n; best = at; }
  }
  return best;
}

// The dates the last answer covered ("Sunday Oct 25", "March 2025"), resolved on the server. The map can show
// exactly those, from a chip beside the presets, so what the answer cites is on the map; the presets switch back.
type Span = { start: string; end: string; label: string };
type MapWindow = number | "answer" | "replay";
const spanKey = (w: number | Span) => (typeof w === "number" ? `${w}` : `${w.start}|${w.end}`);

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

// Shapes parsed once per record: the replay redraws the city at every step of its slider.
const SHAPES = new Map<string, GeoJSON.Geometry | null>();
function shapeOf(e: PennEvent): GeoJSON.Geometry | null {
  const key = `${e.event_id}|${e.geometry_wkt.length}`;
  if (!SHAPES.has(key)) SHAPES.set(key, wktToGeometry(e.geometry_wkt));
  return SHAPES.get(key)!;
}

function toFeatures(events: PennEvent[]): GeoJSON.Feature[] {
  return events.flatMap(e => {
    const g = shapeOf(e);
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

// The answer's arrival. The highlighter is a gradient along each cited line's own length (line-progress, from the
// source's lineMetrics): yellow up to p of the way, clear past it, so moving p from 0 to 1 draws the stroke from
// the line's start to its end.
const HL_CLEAR = "rgba(255, 212, 59, 0)";
const stroke = (p: number, ink = HIGHLIGHT, clear = HL_CLEAR) => (p >= 1 ? ["interpolate", ["linear"], ["line-progress"], 0, ink, 1, ink]
  : ["step", ["line-progress"], ink, Math.max(p, 1e-4), clear]) as maplibregl.ExpressionSpecification;
// The selected record's dark outline (selected-halo), drawn along the street like the highlighter.
const INK = "#1B2433", INK_CLEAR = "rgba(27, 36, 51, 0)";

/** A record's blocks often come as separate pieces (a MultiLineString), each with its own line-progress, so a
 *  stroke along them starts on every piece at once and seems to begin mid-street. Pieces that meet end to end
 *  (within about 3 m) are joined into one line first, turned as needed, so the stroke runs from one end to the
 *  other. Pieces that do not meet stay apart. */
function joined(g: GeoJSON.Geometry): GeoJSON.Geometry {
  if (g.type !== "MultiLineString") return g;
  const meet = (a: GeoJSON.Position, b: GeoJSON.Position) => Math.abs(a[0] - b[0]) < 3e-5 && Math.abs(a[1] - b[1]) < 3e-5;
  const left = g.coordinates.filter(part => part.length > 1).map(part => [...part]);
  const lines: GeoJSON.Position[][] = [];
  while (left.length) {
    let line = left.shift()!;
    for (let grew = true; grew;) {
      grew = false;
      for (let i = 0; i < left.length; i++) {
        const part = left[i], start = line[0], end = line[line.length - 1];
        if (meet(end, part[0])) line = [...line, ...part.slice(1)];
        else if (meet(end, part[part.length - 1])) line = [...line, ...[...part].reverse().slice(1)];
        else if (meet(start, part[part.length - 1])) line = [...part, ...line.slice(1)];
        else if (meet(start, part[0])) line = [...[...part].reverse(), ...line.slice(1)];
        else continue;
        left.splice(i, 1);
        grew = true;
        break;
      }
    }
    lines.push(line);
  }
  return lines.length === 1 ? { type: "LineString", coordinates: lines[0] } : { type: "MultiLineString", coordinates: lines };
}
const joinedFeatures = (fs: GeoJSON.Feature[]) => fs.map(f => ({ ...f, geometry: joined(f.geometry) }));
const NONE = ["==", ["get", "event_id"], ""] as maplibregl.FilterSpecification;
// Slow enough to be seen along a single short block: the arrival's stroke, a citation's replay, and the moment the
// citation numbers glow (as the arrival's stroke is mostly drawn).
const STROKE_MS = 1400, TRACE_MS = 900, GLOW_AT_MS = 1000;
/** Run frame from 0 to 1 over ms, easing out; without motion, only its last frame. Returns a stop. */
function animate(ms: number, frame: (p: number) => void): () => void {
  if (reduceMotion()) { frame(1); return () => {}; }
  let raf = 0;
  const t0 = performance.now();
  // The clock read in each frame, not the frame's own timestamp: that marks when the frame began, which can be
  // before t0, and a progress below 0 eases to values out of range (an opacity of 1.06).
  const tick = () => {
    const x = Math.max(0, Math.min(1, (performance.now() - t0) / ms));
    frame(1 - (1 - x) ** 3);
    if (x < 1) raf = requestAnimationFrame(tick);
  };
  frame(0);
  raf = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(raf);
}
/** One yellow ring out of each cited pin (ids), or of all of them (null): a pin has no length to draw along. */
function ring(m: maplibregl.Map, ids: string[] | null): () => void {
  if (!m.getLayer("cite-ring") || reduceMotion()) return () => {};
  m.setFilter("cite-ring", ids ? ["all", isPoint, ["in", ["get", "event_id"], ["literal", ids]]] as maplibregl.FilterSpecification : isPoint);
  return animate(700, p => {
    m.setPaintProperty("cite-ring", "circle-radius", 8 + 22 * p);
    m.setPaintProperty("cite-ring", "circle-stroke-opacity", Math.min(1, Math.max(0, 1 - p)));
  });
}
const isNarrow = () => typeof window !== "undefined" && window.matchMedia("(max-width: 720px)").matches;
// From this width the answer is a column down the left (see .sheet in globals.css), not a sheet over the bottom.
const hasRail = () => typeof window !== "undefined" && window.matchMedia("(min-width: 900px)").matches;
const RAIL_W = 400;     // --rail-w
const TOPBAR_H = 50;    // --topbar-h
const REPLAY_H = 128;   // --replay-h: the replay bar under the top card (116), and its 12px gap
const KEY_W = 264;      // .legend width
const BOTTOM_H = 94;    // --bar-h on a wide screen: the floating ask box, its gap, and the fine-print line under it
/** The room the ask box and the fine print take along the bottom (--bar-h, which changes with the width): from
 *  the ask box's top edge down. Before the page is laid out, the wide-screen value. */
const bottomRoom = () => {
  const top = typeof document !== "undefined" ? document.querySelector(".askbar")?.getBoundingClientRect().top : undefined;
  return top ? window.innerHeight - top : BOTTOM_H;
};

export default function Page() {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const known = useRef<Map<string, PennEvent>>(new Map());
  const dockRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const pinMarker = useRef<maplibregl.Marker | null>(null);
  const askSeq = useRef(0);
  const loadSeq = useRef(0);

  const [win, setWin] = useState<MapWindow>(7);
  const [answerSpan, setAnswerSpan] = useState<Span | null>(null);
  const [busyDays, setBusyDays] = useState<Busiest | null>(null);           // the replay's 30 days, by how busy
  const [replayAt, setReplayAt] = useState<number | null>(null);    // the slider's time, ms
  const [playing, setPlaying] = useState(false);
  const [events, setEvents] = useState<PennEvent[]>([]);
  const [zoomedOut, setZoomedOut] = useState(false);   // below MINOR_ZOOM: the minor kinds are not drawn
  const [turn, setTurn] = useState(0);   // how many times the ask box's example has rolled
  const [mapLoading, setMapLoading] = useState(true);
  const [mapError, setMapError] = useState("");
  const [loaded, setLoaded] = useState<Record<string, string>>({});   // source -> last successful load (p4-04)

  const [question, setQuestion] = useState("");
  // The judge gate (components/RoleGate.tsx): undefined until this browser's saved choice is read, so the page as
  // first served, and with JavaScript off, shows the gate. The server checks the judge code on every question.
  const [role, setRole] = useState<Role | null | undefined>(undefined);
  const [gateNotice, setGateNotice] = useState("");
  useEffect(() => setRole(savedRole()?.role ?? null), []);
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
  const [, setCitedLoaded] = useState(0);
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
  // The full disclaimer, sources and privacy note, opened from the fine-print line.
  const [aboutOpen, setAboutOpen] = useState(false);
  const [narrow, setNarrow] = useState(false);
  // The top bar's width, for the replay card under it (--topbar-w): one block, its right corners on one edge.
  // It widens and narrows smoothly as the answer's dates come and go, and the replay card follows it.
  const [topbarEl, setTopbarEl] = useState<HTMLElement | null>(null);
  useSmoothSize(topbarEl, { width: true });
  useEffect(() => {
    if (!topbarEl) return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty("--topbar-w", `${topbarEl.offsetWidth}px`));
    ro.observe(topbarEl);
    return () => ro.disconnect();
  }, [topbarEl]);
  // The answer grows as it arrives and folds when hidden; the About card and the replay card fade out, not off.
  const [sheetEl, setSheetEl] = useState<HTMLElement | null>(null);
  useSmoothSize(sheetEl);
  const about = usePresence(aboutOpen);
  const replayCard = usePresence(win === "replay");
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
  const answerSpanRef = useRef(answerSpan); answerSpanRef.current = answerSpan;
  const replayAtRef = useRef(replayAt); replayAtRef.current = replayAt;
  const busyRef = useRef(busyDays); busyRef.current = busyDays;
  const busyReq = useRef<Promise<Busiest | null> | null>(null);   // /busiest, asked once when the replay opens
  // Showing an answer's dates: the server already kept what is active then, so a past window's records
  // (all over by today) stay drawn.
  const exactSpan = useRef(false);
  // Replaying: the map's clock is the slider's (setReplayClock), and a record shows from a week before it
  // starts until it is over.
  const replaying = useRef(false);
  const onMap = useCallback((e: PennEvent) => (replaying.current ? !isOver(e) && daysAway(e) <= REPLAY_AHEAD_DAYS
    : exactSpan.current || !isOver(e)), []);
  const pickingRef = useRef(picking); pickingRef.current = picking;
  const panelOpenRef = useRef(panelOpen); panelOpenRef.current = panelOpen;
  const sheetUpRef = useRef(false); sheetUpRef.current = status !== "idle";

  /** Room the floating UI takes, so a fitted place lands in the visible part of the map. */
  const padding = useCallback((expectSheet = false): maplibregl.PaddingOptions => {
    const narrow = isNarrow();
    if (hasRail()) {
      // The answer is a column on the left, the key stands in the bottom right and the ask box floats between
      // them along the bottom, so the place goes above the ask box, between the two.
      const panel = panelOpenRef.current ? 380 : 0;
      return { top: 12 + TOPBAR_H + 24 + (winRef.current === "replay" ? REPLAY_H : 0), bottom: bottomRoom() + 16,
        left: expectSheet || sheetUpRef.current ? 12 + RAIL_W + 32 : 48,
        right: panel ? panel + 40 : 12 + KEY_W + 36 };
    }
    // The map runs to the bottom edge; the answer sheet, legend, ask box and fine print sit over it, from the
    // top of the dock down. While an answer loads, its sheet is still short, so plan for the height it will grow
    // to (see .sheet max-height), over the ask box and fine print.
    const grown = expectSheet ? (narrow ? 0.48 * window.innerHeight : Math.min(0.46 * window.innerHeight, 460)) : 0;
    const top = dockRef.current?.getBoundingClientRect().top ?? window.innerHeight;
    const dock = Math.max(window.innerHeight - top, grown + bottomRoom());
    const panel = panelOpenRef.current && !narrow ? 380 : 0;
    const replay = winRef.current === "replay" ? REPLAY_H : 0;
    return { top: (narrow ? 64 : 100) + replay, left: narrow ? 20 : 48, right: panel + (narrow ? 20 : 64), bottom: dock + 20 };
  }, []);

  const fly = useCallback((b: maplibregl.LngLatBoundsLike, maxZoom = 17, expectSheet = false) => {
    map.current?.fitBounds(b, { padding: padding(expectSheet), maxZoom, duration: reduceMotion() ? 0 : 1600, essential: true });
  }, [padding]);

  // The whole city's records for each time window, fetched once (about 2,600 records, 300 KB gzipped)
  // and kept for CITY_TTL_MS. Panning and zooming never go back to the server, so nothing pops in at the
  // edges; a window's request is shared while it is in flight.
  const cityCache = useRef(new Map<string, { at: number; req: Promise<CityLayer> }>());
  const cityEvents = useRef<PennEvent[]>([]);
  // Matched pairs, both ways: a Ticketmaster event's visitphilly pick, and a pick's Ticketmaster dates.
  const pickOf = useRef(new Map<string, string>());
  const salesOf = useRef(new Map<string, string[]>());
  // One show at one venue on several dates is one pin: every date's record maps to the whole run, and only
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
   *  folded into the Ticketmaster pin (unless ticketed events are switched off, when the pick shows alone). */
  const drawn = useCallback(() => cityEvents.current.filter(e => !hiddenRef.current.has(category(e)) && !folded(e)
    && onMap(e)), [folded, onMap]);

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

  const fetchCity = useCallback((w: number | Span): Promise<CityLayer> => {
    const key = spanKey(w);
    const hit = cityCache.current.get(key);
    if (hit && Date.now() - hit.at < CITY_TTL_MS) return hit.req;
    const { start, end } = typeof w === "number" ? range(w) : w;
    const req = getJSON<CityLayer>(`/map?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    cityCache.current.set(key, { at: Date.now(), req });
    req.catch(() => cityCache.current.delete(key));
    return req;
  }, []);

  /** The legend's "in view" counts: records whose centroid is on screen, as the server's box used to pick. */
  const countInView = useCallback(() => {
    const m = map.current;
    if (!m) return;
    const b = m.getBounds();
    const out = m.getZoom() < MINOR_ZOOM;
    setZoomedOut(out);
    // A pick folded into its Ticketmaster pin, or a show's later dates, are not counted again; switched-off
    // rows keep their counts. Zoomed out, the key counts only what is drawn: no minor lines.
    setEvents(cityEvents.current.filter(e => e.centroid_lon != null && e.centroid_lat != null && !folded(e)
      && onMap(e) && !(out && isMinor(e)) && b.contains([e.centroid_lon, e.centroid_lat])));
  }, [folded, onMap]);

  const load = useCallback(() => {
    const m = map.current;
    if (!m || !m.getSource("events")) return;
    const seq = ++loadSeq.current;
    const replay = winRef.current === "replay" ? replaySpan() : null;
    // Records up to a week past today come too, to fade in at the slider's right edge as on the days before it.
    const span: Span | null = replay
      ? { start: new Date(replay.start).toISOString(), end: new Date(replay.end + REPLAY_AHEAD_DAYS * 864e5).toISOString(),
          label: `Last ${REPLAY_DAYS} days` }
      : winRef.current === "answer" ? answerSpanRef.current : null;
    const w = span ?? (typeof winRef.current === "number" ? winRef.current : 7);
    setMapLoading(true);
    // The replay opens on the busiest day, so it waits for the day counts as well as the records.
    (replay ? Promise.all([fetchCity(w), busyReq.current]).then(([layer]) => layer) : fetchCity(w))
      .then(({ events, truncated, same_as, covered }) => {
        if (seq !== loadSeq.current) return;
        exactSpan.current = span !== null;
        replaying.current = replay !== null;
        // The replay opens on the busiest day (its busiest step), once its records are here to count.
        const at = replay ? replayAtRef.current ?? peakStep(events, busyRef.current?.busiest ?? replay.startDay) : null;
        setReplayClock(at, REPLAY_STEP);
        if (replay && replayAtRef.current === null) setReplayAt(at);
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
        PRESETS.forEach(p => { if (p.days !== w) fetchCity(p.days).catch(() => {}); });
      })
      .catch(err => { if (seq === loadSeq.current) setMapError(`The map could not load permits: ${err.message}. Check that the PENN API is running.`); })
      .finally(() => { if (seq === loadSeq.current) setMapLoading(false); });
  }, [fetchCity, countInView, drawCity]);

  // A picked record's outline is drawn along its street from one end to the other once the camera is still,
  // and a picked pin's ring fades in, instead of both appearing at once.
  const stopHalo = useRef<() => void>(() => {});
  const drawSelected = useCallback((features: GeoJSON.Feature[]) => {
    const m = map.current;
    if (!m?.getLayer("selected-halo")) return;
    stopHalo.current();
    m.setPaintProperty("selected-halo", "line-gradient", stroke(0, INK, INK_CLEAR));
    m.setPaintProperty("selected-point", "circle-stroke-opacity", 0);
    (m.getSource("selected") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: joinedFeatures(features) });
    const go = () => {
      m.setPaintProperty("selected-point", "circle-stroke-opacity", 1);
      stopHalo.current = animate(TRACE_MS, p => m.setPaintProperty("selected-halo", "line-gradient", stroke(p, INK, INK_CLEAR)));
    };
    if (m.isMoving()) m.once("moveend", go); else go();
  }, []);
  const drawSelectedRef = useRef(drawSelected); drawSelectedRef.current = drawSelected;

  const select = useCallback((e: PennEvent, opts: { focus?: boolean } = {}) => {
    setSelected(e);
    setPanelOpen(true);
    setDetailError("");
    const m = map.current;
    const features = toFeatures([e]);
    if (opts.focus && m) {
      const b = boundsOf(features);
      const view = m.getBounds();
      if (b && !(view.contains(b.getSouthWest()) && view.contains(b.getNorthEast()))) {
        panelOpenRef.current = true;
        fly(b, 17);
      }
    }
    drawSelected(features);
    setDetailLoading(true);
    getJSON<PennEvent>(`/events/${encodeURIComponent(e.event_id)}`)
      .then(full => { known.current.set(full.event_id, full); setSelected(cur => cur?.event_id === full.event_id ? full : cur); })
      .catch(err => setDetailError(`Showing the copy loaded with the map; could not refresh it: ${err.message}.`))
      .finally(() => setDetailLoading(false));
  }, [fly, drawSelected]);

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
      fitBoundsOptions: { padding: { top: 60, bottom: BOTTOM_H + 16, left: 20, right: 20 } },
      minZoom: 10, maxZoom: 19, attributionControl: false,
    });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    // No attribution control: the map credits are in the fine print along the page's bottom edge.

    const init = () => {
      if (m.getSource("events")) return;
      const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
      m.addSource("events", { type: "geojson", data: empty, promoteId: "event_id" });
      m.addSource("cited", { type: "geojson", data: empty, lineMetrics: true });   // line-progress, for the stroke
      m.addSource("selected", { type: "geojson", data: empty, lineMetrics: true });   // line-progress, for its outline
      const pin = new Image(PIN_PX.w, PIN_PX.h);
      pin.onload = () => { if (!m.hasImage(PIN_ICON)) m.addImage(PIN_ICON, pin, { pixelRatio: PIN_PX.ratio }); };
      pin.src = pinDataUrl(PIN_PX.w, PIN_PX.h);
      const ticket = new Image(PIN_PX.w, PIN_PX.h);
      ticket.onload = () => { if (!m.hasImage(TICKET_ICON)) m.addImage(TICKET_ICON, ticket, { pixelRatio: PIN_PX.ratio }); };
      ticket.src = pinDataUrl(PIN_PX.w, PIN_PX.h, "ticketed");
      // Under the basemap's labels, so street names stay readable on top of the permits.
      const below = m.getStyle().layers.find(l => l.type === "symbol")?.id;
      const add = (l: maplibregl.LayerSpecification) => m.addLayer(l, below);
      const round = { "line-cap": "round", "line-join": "round" } as const;
      // The ground: limestone land, buildings a step darker, blue-green rivers (see GROUND in lib/api.ts).
      const fill = (id: string, prop: PaintProp, v: string) => { if (m.getLayer(id)) m.setPaintProperty(id, prop, v); };
      fill("background", "background-color", GROUND.land);
      fill("landuse_residential", "fill-color", GROUND.land);
      fill("building", "fill-color", GROUND.building);
      fill("building", "fill-outline-color", GROUND.buildingEdge);
      fill("water", "fill-color", GROUND.water);
      fill("waterway", "line-color", GROUND.waterway);
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
      // Parks, squares, fields and woods sage inside the city only. Positron draws parks first, right over its background; a
      // sage copy of its woods goes there too (the original, drawn later, is hidden), then the city mask: the
      // background color everywhere past the city line, over the sage and under water, roads and labels.
      if (m.getLayer("park")) m.setPaintProperty("park", "fill-color", PARK.park);
      const wood = m.getStyle().layers.find(l => l.id === "landcover_wood") as maplibregl.FillLayerSpecification | undefined;
      const firstAbovePark = m.getLayer("water") ? "water" : undefined;
      if (wood) {
        m.addLayer({ ...wood, id: "wood-city", paint: { ...wood.paint, "fill-color": PARK.wood } }, firstAbovePark);
        m.setLayoutProperty("landcover_wood", "visibility", "none");
      }
      // Green spaces Positron never draws, in the same sage as parks: squares and lawns are grass landcover
      // (Washington Square is grass/park there, not in the park layer), and ballfields, playgrounds and running
      // tracks are landuse.
      const tiles = wood?.source ?? "openmaptiles";
      m.addLayer({ id: "grass-city", type: "fill", source: tiles, "source-layer": "landcover",
        filter: ["==", ["get", "class"], "grass"], paint: { "fill-color": PARK.park } }, firstAbovePark);
      m.addLayer({ id: "fields-city", type: "fill", source: tiles, "source-layer": "landuse",
        filter: ["in", ["get", "class"], ["literal", ["pitch", "playground", "track"]]], paint: { "fill-color": PARK.park } }, firstAbovePark);
      m.addSource("city-mask", { type: "geojson", data: CITY_MASK_URL });
      m.addLayer({ id: "city-mask", type: "fill", source: "city-mask",
        paint: { "fill-color": GROUND.land } },
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
      add({ id: "cited-glow", type: "line", source: "cited", filter: isLine, layout: round,
        paint: { "line-gradient": stroke(1), "line-width": zoomWidth(12, 30), "line-opacity": 0.85, "line-blur": 1 } });
      // The answer's records that load after the first stroke (the rest of what it touched): stroked on arrival.
      add({ id: "cite-late", type: "line", source: "cited", filter: NONE, layout: round,
        paint: { "line-gradient": stroke(1), "line-width": zoomWidth(12, 30), "line-opacity": 0.85, "line-blur": 1 } });
      // One record's stroke drawn again while its citation is pointed at (the glow leaves that record to it).
      add({ id: "cite-trace", type: "line", source: "cited", filter: NONE, layout: round,
        paint: { "line-gradient": stroke(1), "line-width": zoomWidth(12, 30), "line-opacity": 0.85, "line-blur": 1 } });
      add({ id: "selected-halo", type: "line", source: "selected", filter: isLine, layout: round,
        paint: { "line-gradient": stroke(1, INK, INK_CLEAR), "line-width": zoomWidth(9, 18), "line-opacity": 0.9 } });
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
      // The answer's own lines over every city layer, in the same weights as the city's (CITED_WIDTH): thick for a
      // closed street, thin for one lane, dots for the sidewalk, at every zoom, so the extent still reads under the
      // highlighter. A white edge under the closed and one-lane lines keeps them clear of the yellow.
      const fade = { "line-opacity-transition": { duration: reduceMotion() ? 0 : 300 } };
      add({ id: "cited-casing", type: "line", source: "cited", filter: ["all", isLine, notWalk] as maplibregl.FilterSpecification, layout: round,
        paint: { "line-opacity": todayOpacity, "line-color": "#ffffff", "line-width": CITED_EDGE, ...fade } });
      add({ id: "cited-line", type: "line", source: "cited", filter: ["all", isLine, notWalk] as maplibregl.FilterSpecification, layout: round,
        paint: { "line-opacity": todayOpacity, "line-color": ["get", "color"], "line-width": CITED_WIDTH, ...fade } });
      add({ id: "cited-walk", type: "line", source: "cited", filter: ["all", isLine, isWalk] as maplibregl.FilterSpecification,
        layout: { "line-cap": "round" },
        paint: { "line-opacity": todayOpacity, "line-color": ["get", "color"], "line-width": zoomWidth(2.4, 5),
          "line-dasharray": [0.05, 2], ...fade } });
      // The late records' own lines, apart so they can fade in under their stroke while the first ones stay.
      add({ id: "cite-late-line", type: "line", source: "cited", filter: NONE, layout: round,
        paint: { "line-opacity": 0, "line-color": ["get", "color"], "line-width": CITED_WIDTH, ...fade } });
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

      // Listings (visitphilly, Ticketmaster): pins over everything, street names included, so a pin on a closed
      // street stays visible and clickable. Under each pin's tip, a highlighter dot when cited and a ring when open.
      m.addLayer({ id: "cited-point", type: "circle", source: "cited", filter: isPoint,
        paint: { "circle-color": HIGHLIGHT, "circle-radius": zoomWidth(9, 14), "circle-opacity": 0.85, "circle-blur": 0.2,
          "circle-opacity-transition": { duration: reduceMotion() ? 0 : 300 } } });
      m.addLayer({ id: "cite-ring", type: "circle", source: "cited", filter: NONE,
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": 8, "circle-stroke-color": "#F2B705", "circle-stroke-width": 2.5,
          "circle-stroke-opacity": 0 } });
      m.addLayer({ id: "selected-point", type: "circle", source: "selected", filter: isPoint,
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": zoomWidth(8, 12), "circle-stroke-color": "#1B2433", "circle-stroke-width": 2.5,
          "circle-stroke-opacity-transition": { duration: reduceMotion() ? 0 : 240 } } });
      m.addLayer({ id: "listings", type: "symbol", source: "events", filter: ["in", ["get", "cat"], ["literal", ["listing", "ticketed"]]],
        layout: { "icon-image": ["match", ["get", "cat"], "ticketed", TICKET_ICON, PIN_ICON], "icon-anchor": "bottom", "icon-offset": [0, PIN_PX.tipY],
          "icon-allow-overlap": true, "icon-ignore-placement": true, "symbol-sort-key": ["-", 0, ["get", "days"]] },   // today's on top
        paint: { "icon-opacity": pinOpacity } });
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
      });
      // While an answer is open, its records come before the greyed ones under them: a closure in effect today
      // must not answer for the cited route drawn over it.
      const cited = new Set(focusRef.current ?? []);
      const outside = (e: PennEvent) => (cited.size && !cited.has(e.event_id) ? 1 : 0);
      found.sort((a, b) => outside(a) - outside(b) || pickOrder(a) - pickOrder(b) || byWhen(a, b));
      // On a pin or dot, only the markers under the cursor count: the street line beneath is not what was aimed at.
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
      setChoicesRange(null);
      setSelected(null);
      setPanelOpen(true);
      drawSelectedRef.current(toFeatures(h));
    });
    let t: ReturnType<typeof setTimeout>;
    m.on("moveend", () => { clearTimeout(t); t = setTimeout(countInView, 150); });
    return () => { clearTimeout(t); m.remove(); map.current = null; };   // StrictMode mounts twice in development
  }, [load, countInView, select, closePanel]);

  useEffect(() => { load(); }, [win, answerSpan, load]);

  // The slider moved: the map's clock follows, a show's next date leads its run again, and the city redraws.
  useEffect(() => {
    if (win !== "replay" || replayAt === null || !replaying.current) return;
    setReplayClock(replayAt, REPLAY_STEP);
    groupSeries(cityEvents.current, seriesOf.current, leads.current);
    drawCity();
    countInView();
  }, [win, replayAt, drawCity, countInView]);

  // Play: a step at a time to today. Leaving the replay stops it and gives the map its own clock back.
  useEffect(() => {
    if (!playing || win !== "replay") return;
    const { end } = replaySpan();
    const t = setInterval(() => setReplayAt(at => {
      const next = (at ?? end) + REPLAY_STEP;
      if (next >= end) { setPlaying(false); return end - REPLAY_STEP; }
      return next;
    }), REPLAY_TICK_MS);
    return () => clearInterval(t);
  }, [playing, win]);
  useEffect(() => {
    if (win === "replay") return;
    setPlaying(false);
    if (replaying.current) { replaying.current = false; setReplayClock(null); }
  }, [win]);

  /** Open the replay: the busy days of the last 30 first (for the bars and the opening day), then its records. */
  const startReplay = useCallback(() => {
    const { startDay, endDay } = replaySpan();
    busyReq.current ??= getJSON<Busiest>(`/busiest?start=${startDay}&end=${endDay}`)
      .then(b => { setBusyDays(b); busyRef.current = b; return b; })
      .catch(() => { busyReq.current = null; return null; });   // the bars are missing; the slider still works
    setWin("replay");
  }, []);

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
        if (aboutOpen) setAboutOpen(false);
        else if (picking) setPicking(false);
        else if (panelOpenRef.current) closePanel();
      } else if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [aboutOpen, picking, closePanel]);

  // ---- asking ----
  /** The answer's records on the map. The first `lead` ids are the ones its text cites: they are drawn, framed
   *  and stroked as soon as they load, and the rest of what the answer touched joins them quietly after. */
  async function showCited(ids: string[], fitIfNoPlace: boolean, extra: maplibregl.LngLatBounds | null = null,
    lead = ids.length, stale: () => boolean = () => false) {
    const m = map.current;
    // Each record goes into known as it arrives, and the card redraws (known is a ref), so the headline and the
    // citations' titles need not wait for the slowest record either.
    // Most of them are on the map already (the city layer for these dates): taken from there at once, the rest
    // fetched together in one request (an event's past routes are never on the map: dozens of records).
    const onMap = new Map(cityEvents.current.map(e => [e.event_id, e]));
    const load = async (some: string[]) => {
      const have = (id: string) => known.current.get(id) ?? onMap.get(id);
      const got = await getEvents(some.filter(id => !have(id))).catch(() => [] as PennEvent[]);
      got.forEach(e => known.current.set(e.event_id, e));
      if (got.length) setCitedLoaded(n => n + 1);
      const evs = some.flatMap(id => have(id) ?? []);
      evs.forEach(e => known.current.set(e.event_id, e));
      return evs;
    };
    const draw = (evs: PennEvent[]) => {
      const features = joinedFeatures(toFeatures(evs)).map(f => ({ ...f, properties: { ...f.properties, color: COLOR[f.properties!.cat as Category] } }));
      (m?.getSource("cited") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
      return features;
    };
    const wanted = ids.slice(0, MAX_CITED_ON_MAP);
    const first = await load(wanted.slice(0, Math.max(lead, 1)));
    if (stale()) return;
    // Nothing of them shows until the camera is still: then they appear and the stroke draws in, once.
    hideCited();
    const features = draw(first);
    let b = fitIfNoPlace ? boundsOf(features) : null;
    if (fitIfNoPlace && extra) b = b ? b.extend(extra) : extra;   // the landmarks the answer names, too
    if (b) fly(b, 16.5);
    if (m) { if (m.isMoving()) m.once("moveend", arrive); else arrive(); }
    if (wanted.length <= lead) return;
    const rest = await load(wanted.slice(Math.max(lead, 1)));
    if (stale()) return;
    draw([...first, ...rest]);
    // In before the first stroke: they draw with it. After it: they draw on their own layer, the first one kept.
    if (!arrived.current || !m) return;
    late.current = rest.map(e => e.event_id).filter(id => !first.some(e => e.event_id === id));
    setFilters();
    m.setPaintProperty("cite-late-line", "line-opacity", todayOpacity);
    stopLate.current();
    stopLate.current = animate(STROKE_MS, p => m.setPaintProperty("cite-late", "line-gradient", stroke(p)));
    stopRing.current();
    stopRing.current = ring(m, late.current);
  }

  const stopStroke = useRef<() => void>(() => {});
  const stopLate = useRef<() => void>(() => {});
  const stopTrace = useRef<() => void>(() => {});
  const stopRing = useRef<() => void>(() => {});
  const arrived = useRef(false);                     // the first stroke of this answer has started
  const late = useRef<string[]>([]);                 // records stroked after it, on cite-late
  const pointed = useRef<string[] | null>(null);     // the records of the citation pointed at, on cite-trace
  /** The three highlighter layers share the answer's lines: the first stroke, the late records, and the
   *  citation pointed at, which the other two leave to it. */
  function setFilters() {
    const m = map.current;
    if (!m?.getLayer("cite-late")) return;
    const among = (ids: string[]) => ["in", ["get", "event_id"], ["literal", ids]] as maplibregl.ExpressionSpecification;
    const not = (ids: string[] | null) => (ids?.length ? [["!", among(ids)]] : []) as maplibregl.ExpressionSpecification[];
    const all = (...f: unknown[]) => ["all", ...f] as maplibregl.FilterSpecification;
    m.setFilter("cited-glow", all(isLine, ...not(late.current), ...not(pointed.current)));
    m.setFilter("cited-casing", all(isLine, notWalk));
    m.setFilter("cited-line", all(isLine, notWalk, ...not(late.current)));
    m.setFilter("cited-walk", all(isLine, isWalk));
    m.setFilter("cite-late-line", late.current.length ? all(isLine, notWalk, among(late.current)) : NONE);
    m.setFilter("cite-late", late.current.length ? all(isLine, among(late.current), ...not(pointed.current)) : NONE);
    m.setFilter("cite-trace", pointed.current ? all(isLine, among(pointed.current)) : NONE);
  }
  /** Before an answer's records arrive: no stroke, no line, no dot, so the first thing seen is the stroke drawn. */
  function hideCited() {
    const m = map.current;
    if (!m?.getLayer("cited-glow")) return;
    stopStroke.current(); stopLate.current();
    arrived.current = false;
    late.current = [];
    setFilters();
    m.setPaintProperty("cited-glow", "line-gradient", stroke(0));
    ["cited-casing", "cited-line", "cited-walk", "cite-late-line"].forEach(id => m.setPaintProperty(id, "line-opacity", 0));
    m.setPaintProperty("cited-point", "circle-opacity", 0);
    stopRing.current();
    m.setPaintProperty("cite-ring", "circle-stroke-opacity", 0);
  }
  /** The camera is still: the lines and dots fade in, the stroke draws, the pins ring, then the numbers glow. */
  function arrive() {
    const m = map.current;
    if (!m?.getLayer("cited-glow") || arrived.current) return;
    arrived.current = true;
    ["cited-casing", "cited-line", "cited-walk"].forEach(id => m.setPaintProperty(id, "line-opacity", todayOpacity));
    m.setPaintProperty("cited-point", "circle-opacity", 0.85);
    stopStroke.current();
    stopStroke.current = animate(STROKE_MS, p => m.setPaintProperty("cited-glow", "line-gradient", stroke(p)));
    stopRing.current();
    stopRing.current = ring(m, null);
    setTimeout(() => glow(document.querySelectorAll(".answer-text .cite")), reduceMotion() ? 0 : GLOW_AT_MS);
  }
  /** A citation pointed at (hovered or focused): its records drawn again, their pins rung; null puts the
   *  highlighter back over every cited record. */
  function trace(ids: string[] | null) {
    const m = map.current;
    if (!m?.getLayer("cite-trace")) return;
    stopTrace.current();
    pointed.current = ids;
    setFilters();
    if (!ids) return;
    stopTrace.current = animate(TRACE_MS, p => m.setPaintProperty("cite-trace", "line-gradient", stroke(p)));
    stopRing.current();
    stopRing.current = ring(m, ids);
  }

  async function ask(q: string, at: [number, number] | null = pin) {
    q = q.trim();
    if (!q) return;
    const seq = ++askSeq.current;
    const stale = () => seq !== askSeq.current;
    trace(null);
    hideCited();
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
      // The map switches to the answer's dates, unless they are the default next 7 days the presets already show.
      if (r.start && r.end && r.window && !r.window.startsWith("the next 7 days")) {
        setAnswerSpan({ start: r.start, end: r.end, label: r.window });
        setWin("answer");
      }
      const view: View | null | undefined = r.view;
      if (!flew && view) { flew = true; fly(view.bounds, 17, true); }
      if (!at && view?.point) setPlacePoint(view.point);   // a picked point already has its pin
      // Settle once the answer has its real height, so the place sits clear of the sheet.
      const settle = placeView ?? view;
      if (settle) requestAnimationFrame(() => requestAnimationFrame(() => { if (!stale()) fly(settle.bounds); }));
      // The answer's own citations first (stroked on arrival), then what it touched, then the rest of what the search
      // found on the streets at the place: a closure beside a place belongs on its map even when the prose skips it.
      const ids = Array.from(new Set([...(r.answer ? citationOrder(r.answer) : []), ...(r.cited ?? []), ...(r.nearby ?? [])]));
      setCitedIds(ids);
      // No place in the question: the landmarks the answer names are pinned, and the map frames them with
      // the cited records (one landmark: centered; several: zoomed out to fit all).
      const lms = !flew ? r.landmarks ?? [] : [];
      setMarks(lms.map(l => ({ name: l.name, point: l.point })));
      const lmBounds = lms.reduce<maplibregl.LngLatBounds | null>((acc, l) => {
        const lb = new maplibregl.LngLatBounds(l.bounds[0], l.bounds[1]);
        return acc ? acc.extend(lb) : lb;
      }, null);
      if (ids.length) await showCited(ids, !flew, lmBounds, r.answer ? citationOrder(r.answer).length : ids.length, stale);
      else if (lmBounds) fly(lmBounds, 16);
    } catch (err) {
      if (stale()) return;
      setStatus("error");
      const e = err as Error & { limited?: boolean; badCode?: boolean };
      if (e.badCode) { forgetRole(); setGateNotice(e.message); setRole(null); }   // a stale or wrong judge code
      setReply({ message: e.limited ? e.message
        : `PENN could not answer: ${e.message}. Check that the PENN API is running, then ask again.` });
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
    setAnswerSpan(null);
    setWin(w => (w === "answer" ? 7 : w));
  }

  // A run of citations ("6–16") opens as a list in the panel, like several records under one click.
  const [choicesRange, setChoicesRange] = useState<[number, number] | null>(null);
  const onCites = (ids: string[]) => {
    getEvents(ids.filter(id => !known.current.has(id))).catch(() => [] as PennEvent[])
      .then(got => {
        got.forEach(full => known.current.set(full.event_id, full));
        const recs = ids.flatMap(id => known.current.get(id) ?? []);
        if (!recs.length) return;
        const features = toFeatures(recs);
        setChoices(recs);
        setChoicesRange([citedNumber(ids[0]), citedNumber(ids[ids.length - 1])]);
        setSelected(null);
        setPanelOpen(true);
        drawSelected(features);
        const b = boundsOf(features);
        if (b) { panelOpenRef.current = true; fly(b, 17); }
      });
  };

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
  const dateNote = reply?.window_kind === "future" ? "Upcoming permits may not be filed yet."
    : reply?.window_kind === "past" ? "Past dates: this is what the records show was permitted." : "";
  // The public demo reuses an answer to a question already asked; say when it was first given.
  const savedNote = reply?.cached_at ? `Answered earlier, ${new Date(reply.cached_at).toLocaleString("en-US",
    { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}.` : "";
  const kindNote = [dateNote, savedNote].filter(Boolean).join(" ");
  const when = reply?.window ?? place?.window ?? "";
  // The headline waits for every cited record to be loaded, so it never counts half of them.
  const citedRecs = order.map(id => known.current.get(id));
  const headline = reply?.answer && !busy && order.length && citedRecs.every(Boolean)
    ? answerHeadline(citedRecs as PennEvent[], reply.start, reply.end) : null;

  return (
    <main className={`app${panelOpen ? " panel-open" : ""}${picking ? " picking" : ""}${win === "replay" ? " replaying" : ""}`}>
      <div className="map" ref={mapEl} role="region" aria-label="Map of Philadelphia street permits" />

      <header className="topbar" ref={setTopbarEl}>
        <div className="brand">
          <span className="wordmark">PENN</span>
          <span className="tagline">Philadelphia Events and Neighborhood Navigator</span>
        </div>
        <div className="segmented" role="radiogroup" aria-label="Time on the map">
          {/* Read as one line: "Next 24 hours | 7 days | 30 days | Replay | <the answer's dates>". */}
          {PRESETS.map((p, i) => (
            <button key={p.days} type="button" role="radio" className="seg-window" aria-checked={win === p.days}
              aria-label={`Next ${p.label}`} onClick={() => setWin(p.days)}>
              <span className="seg-long">{i === 0 ? `Next ${p.label}` : p.label}</span><span className="seg-short" aria-hidden="true">{p.days === 1 ? "24h" : `${p.days}d`}</span>
            </button>
          ))}
          <button type="button" role="radio" className="seg-replay" aria-checked={win === "replay"} aria-label="Replay"
            title="Move the map through the last 30 days, opening on the busiest" onClick={startReplay}>
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6.5" r="4.6" fill="none" stroke="currentColor" strokeWidth="1.3" /><path d="M6 4v2.6l1.7 1.1M4.6 1h2.8" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
            <span className="seg-long">Replay</span>
          </button>
          {answerSpan && (
            <button type="button" role="radio" className="seg-date" aria-checked={win === "answer"}
              title="The dates of the last answer" onClick={() => setWin("answer")}>
              <span>{answerSpan.label}</span>
            </button>
          )}
        </div>
        <FreshLine loaded={loaded} />
      </header>

      {replayCard && (() => {
        const { start, end } = replaySpan();
        // Until the 30 days' records are in, the bar stands on the busiest day, waiting.
        const at = replayAt ?? (busyDays?.busiest ? phillyMidnight(busyDays.busiest) : start);
        return (
          <ReplayBar leaving={replayCard === "out"} start={start} end={end} step={REPLAY_STEP} days={busyDays?.days ?? []} busiest={busyDays?.busiest ?? null}
            at={at} loading={replayAt === null} onAt={setReplayAt} playing={playing} onPlay={setPlaying}
            inEffect={busyDuring(cityEvents.current, at)}
            onBusiest={() => { setPlaying(false); if (busyDays?.busiest) setReplayAt(peakStep(cityEvents.current, busyDays.busiest)); }} />
        );
      })()}

      {mapError && <div className="map-error" role="alert">{mapError}</div>}
      {picking && <div className="pick-hint" role="status">Click the map where you mean. Esc cancels.</div>}

      {hover && (
        <div className="hover-tip" style={{ left: hover.x + 14, top: hover.y + 14 }} aria-hidden="true">
          <TipBody e={hover.e} partners={partnersOf(hover.e)} dates={seriesOf.current.get(hover.e.event_id)?.length ?? 1} />
        </div>
      )}

      <PermitPanel open={panelOpen} choices={choices} event={selected} loading={detailLoading} error={detailError}
        citedNumber={citedNumber} range={choicesRange} partnersOf={partnersOf} datesOf={e => seriesOf.current.get(e.event_id) ?? [e]} onPick={e => select(e)} onBack={choices.length > 1 ? () => setSelected(null) : null}
        onClose={closePanel} />

      <div className="dock">
        <div className="dock-row" ref={dockRef}>
          {hasSheet && (
            <section ref={setSheetEl} className={`sheet${sheetOpen ? "" : " collapsed"}`} aria-live="polite" aria-busy={busy}>
              {/* The place's sign, then the result in one line, then what was asked and for when; the answer under
                  a rule, and the guard closing it. Folded, the sign and the headline stay. */}
              <div className="sheet-head">
                {place?.name ? <span className="plate">{place.name}</span>
                  : busy ? <span className="plate pending">Finding the place</span> : <span />}
                <div className="sheet-acts">
                  {onTopic && !busy && (
                    <button type="button" className="text-btn" title="PENN forgets the earlier questions and their place"
                      onClick={forget}>New topic</button>
                  )}
                  <button type="button" className="text-btn sheet-toggle" aria-expanded={sheetOpen}
                    aria-label={sheetOpen ? "Hide answer" : "Show answer"} onClick={() => setSheetOpen(o => !o)}>
                    {sheetOpen ? "Hide" : "Show"}</button>
                </div>
              </div>
              {headline && <h2 className="headline">{headline}</h2>}
              {sheetOpen && <p className="sheet-ctx">&ldquo;{asked}&rdquo;{when ? `, ${when}` : ""}</p>}
              {sheetOpen && (
                <div className="sheet-body">
                  {busy && (
                    <div className="working" role="status">
                      <span className="sweep" aria-hidden="true" />
                      {status === "locating" ? "Finding the place on the map" : `Reading permits${place?.name ? ` near ${place.name}` : ""}`}
                    </div>
                  )}
                  {reply?.message && <p className={reply.outside || status === "error" ? "sheet-error" : "sheet-msg"}>{reply.message}</p>}
                  {reply?.answer && (
                    <>
                      <AnswerText text={reply.answer} titles={titles} selected={selected?.event_id ?? null} onCite={onCite}
                        onCites={onCites} onPoint={trace} web={reply.web} />
                      {reply.asked_back && !pin && (
                        <button type="button" className="text-btn" onClick={() => { pickFor.current = asked; setPicking(true); }}>
                          Pick a point on the map</button>
                      )}
                    </>
                  )}
                </div>
              )}
              {sheetOpen && reply?.answer && <GuardLine guard={reply.guard ?? null} note={kindNote} model={reply.model} />}
            </section>
          )}
          <Legend counts={counts} bands={bands} citedCount={citedIds.length} hoods={showHoods} onHoods={setShowHoods}
            hidden={hiddenCats} onHidden={setHiddenCats} zoomedOut={zoomedOut}
            loading={mapLoading && !events.length} />
          {/* The ask box floats between the answer and the key (on desktop), or under them. */}
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
                placeholder={pin ? "Ask about the pinned point" : narrow ? "Ask about a street or event" : ""} />
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
        </div>
        {/* One line: the demo's date, what PENN is not, and the map credits, which must stay visible. The full
            disclaimer, the sources and the privacy note open from "About, sources and privacy". */}
        <div className="fine-print">
          <p className="fine-row">
            <span className="fine-give">
              {/* On a phone the line keeps the date and "About" (.fine-long drops the rest). */}
              {PINNED_NOW && <strong className="fine-demo">Demo snapshot:<span className="fine-long"> data as of</span>{" "}
                {demoStamp(PINNED_NOW)}</strong>}
              <span className="fine-note">Not an official City of Philadelphia source.</span>{" "}
              <button type="button" className="fine-about" aria-expanded={aboutOpen} aria-controls="about"
                aria-label="About, sources and privacy" onClick={() => setAboutOpen(o => !o)}>
                About<span className="fine-long">, sources and privacy</span></button>
              {role === "visitor" && <>{" · "}<button type="button" className="fine-about"
                onClick={() => { forgetRole(); setGateNotice(""); setRole(null); }}>Judge access</button></>}
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
        {about && (
          <section id="about" className={`about${about === "out" ? " leaving" : ""}`} role="dialog" aria-labelledby="about-title">
            <div className="about-head">
              <h2 id="about-title">About PENN</h2>
              <button type="button" className="icon-btn" aria-label="Close" onClick={() => setAboutOpen(false)}>
                <svg width="12" height="12" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 2l6 6m0-6L2 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
              </button>
            </div>
            <p><strong>Not an official City of Philadelphia source.</strong> Data is shown as published and may be
              incomplete or out of date; check the linked record.</p>
            <h3>Sources</h3>
            <p><a href="https://opendataphilly.org/datasets/street-lane-closures/" target="_blank" rel="noopener">City
              of Philadelphia</a> street permits and event-closure notices, race organizers, <a
              href="https://www.visitphilly.com/" target="_blank" rel="noopener">visitphilly.com</a> and <a
              href="https://www.ticketmaster.com/" target="_blank" rel="noopener">Ticketmaster</a> listings, and web
              pages found with <a href="https://www.tavily.com/" target="_blank" rel="noopener">Tavily</a> search
              when PENN has no record.</p>
            <h3>Built with</h3>
            <p>Answers are written by NVIDIA Nemotron 3 Super, served by <a href="https://tokenfactory.nebius.com/"
              target="_blank" rel="noopener">Nebius Token Factory</a>. When PENN has no record, the web is searched
              with <a href="https://www.tavily.com/" target="_blank" rel="noopener">Tavily</a>. Map tiles by <a
              href="https://openfreemap.org/" target="_blank" rel="noopener">OpenFreeMap</a> © <a
              href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> © <a
              href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>;
              neighborhood outlines © <a href="https://opendataphilly.org/datasets/philadelphia-neighborhoods/"
              target="_blank" rel="noopener">Robert Cheetham</a>, <a href="https://creativecommons.org/licenses/by/4.0/"
              target="_blank" rel="noopener">CC BY 4.0</a>.</p>
            <h3>Privacy</h3>
            {/* Tavily may keep and use the search terms it receives (its terms 6.5, 9.2); penn/web/tavily.py
                sends only a short query or an event name and year, never the question itself. */}
            <p>PENN sets no cookies and shows no ads. Questions you ask are sent to NVIDIA Nemotron on Nebius to
              answer them. When PENN has no record, a short web search (an event&apos;s name and year, or a few
              words on what you asked, never your question itself) is sent to Tavily, which may keep it.</p>
            <p>This browser remembers whether you chose Judge or Visitor (and a judge&apos;s access code) in its own
              storage. To keep the demo within its limits, PENN counts questions per day; that count never includes
              what you asked or your address.</p>
          </section>
        )}
      </div>
      {role !== "judge" && role !== "visitor" && (
        <RoleGate notice={gateNotice} onEnter={(r, q) => { setGateNotice(""); setRole(r); if (q) ask(q, null); }} />
      )}
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
  const keep = cited?.length ? ["in", ["get", "event_id"], ["literal", cited]] as Expr : null;
  FOCUS.forEach(({ id, opacity, base, color, casing }) => {
    if (!m.getLayer(id)) return;
    // A white casing under a grey line would read as a road, so it goes; pins stay ink, so they recede.
    const out = casing ? 0 : id === "listings" ? OUT_OF_ANSWER.pin : OUT_OF_ANSWER.opacity;
    const o = (keep ? ["case", keep, base, out] : base) as Expr;
    opacity.forEach(p => m.setPaintProperty(id, p, o));
    if (color) m.setPaintProperty(id, color.prop, keep ? ["case", keep, color.value, OUT_OF_ANSWER.color] as Expr : color.value);
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
  const now = realNow();
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

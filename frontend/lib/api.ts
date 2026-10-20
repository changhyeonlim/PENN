export const API = process.env.NEXT_PUBLIC_PENN_API ?? "http://localhost:8000";

export type PennEvent = {
  event_id: string; source: string; event_type: string; title: string; description: string;
  organization: string; occupancy: string; source_url: string; source_authority: string;
  claim_certainty: string; start_date: string | null; end_date: string | null;
  start_ts: string | null; end_ts: string | null; open_ended: boolean; status: string; geometry_wkt: string;
};

/** Center and bounds of a resolved place, as /locate and /agent return them. */
export type View = { center: [number, number]; bounds: [[number, number], [number, number]] };

export type Located = { found: boolean; place: string | null; view?: View; window?: string };

export type AgentReply = {
  answer?: string; notes?: string[]; needs_pin?: boolean; outside?: boolean; message?: string;
  place?: string | null; window?: string; window_kind?: "before_records" | "past" | "current" | "future";
  start?: string; end?: string; view?: View | null; model?: string | null; tools?: string[]; cited?: string[];
  web?: { id: string; title: string; url: string; site: string }[];   // web pages the answer cites (p5-09)
};

export async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  // fetch rejects with a bare "Failed to fetch" when nothing answers; say which server is missing.
  const r = await fetch(`${API}${path}`, init).catch(() => { throw new Error(`the PENN API at ${API} is not reachable`); });
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? `Request failed (${r.status})`);
  return r.json();
}

export const locate = (q: string) => getJSON<Located>(`/locate?q=${encodeURIComponent(q)}`);

export const askAgent = (body: { question: string; lat?: number; lon?: number }) =>
  getJSON<AgentReply>("/agent", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Parse the WKT the API returns into GeoJSON geometry (points and lines only). */
export function wktToGeometry(wkt: string): GeoJSON.Geometry | null {
  const nums = (s: string) => s.trim().split(/\s*,\s*/).map(p => p.split(/\s+/).map(Number) as [number, number]);
  if (!wkt) return null;
  if (wkt.startsWith("MULTIPOINT")) {   // an event listed at several places
    return { type: "MultiPoint", coordinates: wkt.slice(wkt.indexOf("(") + 1, -1).replace(/[()]/g, "").trim().split(/\s*,\s*/)
      .map(p => p.split(/\s+/).map(Number) as [number, number]) };
  }
  if (wkt.startsWith("POINT")) {
    const [x, y] = wkt.slice(wkt.indexOf("(") + 1, -1).trim().split(/\s+/).map(Number);
    return { type: "Point", coordinates: [x, y] };
  }
  if (wkt.startsWith("MULTILINESTRING")) {
    const inner = wkt.slice(wkt.indexOf("((") + 2, -2);
    return { type: "MultiLineString", coordinates: inner.split(/\)\s*,\s*\(/).map(nums) };
  }
  if (wkt.startsWith("LINESTRING")) return { type: "LineString", coordinates: nums(wkt.slice(wkt.indexOf("(") + 1, -1)) };
  return null;
}

export type Category = "full" | "partial" | "footway" | "parking" | "event" | "listing";

/** Sources of events that happen at a place without closing a street: drawn as flags, never as closures. */
export const LISTING_SOURCES = ["visitphilly"];
export const LISTED_BY: Record<string, string> = { visitphilly: "visitphilly.com" };

/** Map layer category. Footway is styled apart so a sidewalk shed never reads as a road closure. */
export function category(e: PennEvent): Category {
  if (LISTING_SOURCES.includes(e.source)) return "listing";
  if (e.event_type === "event") return "event";
  if (e.event_type === "parking_restriction") return "parking";
  if (e.occupancy === "Full Closure") return "full";
  if (e.occupancy === "Footway") return "footway";
  return "partial";
}

/** Most severe first: the order of the legend, and of the layers from top down. */
export const CATEGORIES: { key: Category; color: string; label: string; detail: string }[] = [
  { key: "full", color: "#C8102E", label: "Street closed", detail: "Full closure permit" },
  { key: "event", color: "#5A3FC0", label: "Event closure", detail: "Announced by the City for an event" },
  { key: "partial", color: "#D46A00", label: "Lane closed", detail: "Partial closure permit" },
  { key: "footway", color: "#3F4A5A", label: "Sidewalk only", detail: "Footway permit; the road stays open" },
  { key: "parking", color: "#0B7A8A", label: "No parking", detail: "Parking or loading restriction" },
  { key: "listing", color: "#1D4ED8", label: "Event listing", detail: "No street closure listed" },
];
/** Plain-language meaning of each category, for the key's explanation. */
export const MEANING: Record<Category, string> = {
  full: "The permit allows closing the whole street to traffic.",
  event: "The City announced the street closes for an event.",
  partial: "The permit allows closing a lane; traffic still gets through.",
  footway: "Sidewalk work, such as a shed or scaffold. The road stays open.",
  parking: "Parking or loading is restricted at the curb.",
  listing: "Something happening at this place, listed by visitphilly.com. A listing is not a street closure.",
};
/** Basemap road colors, set over Positron's so highways read apart from streets (see page.tsx). Every other road
 * class is drawn as one plain street: OpenStreetMap's primary/secondary/tertiary/residential tags change block by
 * block on the same Philadelphia street, so a major-vs-local split looks random. */
export const ROAD = { highway: "#B4C2D3", highwayEdge: "#8798AE", street: "#FFFFFF" };
/** A permit's strength by days from today (see daysAway): distinct steps, not a smooth fade, so each reads apart. */
export const STRENGTH = [
  { from: 0, opacity: 1, label: "In effect today" },
  { from: 1, opacity: 0.62, label: "1 to 3 days from today" },
  { from: 4, opacity: 0.38, label: "4 to 6 days from today" },
  { from: 7, opacity: 0.18, label: "7 or more days from today" },
];
export const FADE_DAYS = STRENGTH[STRENGTH.length - 1].from;
export const strengthBand = (days: number) => STRENGTH.reduce((band, s, i) => (days >= s.from ? i : band), 0);
export const fade = (days: number) => STRENGTH[strengthBand(days)].opacity;
export const COLOR = Object.fromEntries(CATEGORIES.map(c => [c.key, c.color])) as Record<Category, string>;
export const LABEL = Object.fromEntries(CATEGORIES.map(c => [c.key, c.label])) as Record<Category, string>;
export const DETAIL = Object.fromEntries(CATEGORIES.map(c => [c.key, c.detail])) as Record<Category, string>;
export const HIGHLIGHT = "#FFD43B";

/** The listing marker: a blue pennant with a "P" on a pole, white-edged so it reads over red closure lines.
 * One drawing for the map icon and the key. The pole's foot (x 2.5, y 21 of 18 x 22) is the event's spot. */
export function flagSvg(width: number, height: number): string {
  const blue = COLOR.listing, pole = "#12306E";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 18 22">`
    + `<line x1="2.5" y1="2" x2="2.5" y2="21" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/>`
    + `<line x1="2.5" y1="2" x2="2.5" y2="21" stroke="${pole}" stroke-width="1.7" stroke-linecap="round"/>`
    + `<path d="M3.3 1.6H16.6L13.5 6.4L16.6 11.2H3.3Z" fill="${blue}" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/>`
    + `<text x="9.4" y="9.5" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="7.6" font-weight="700" fill="#fff">P</text>`
    + `<circle cx="2.5" cy="21" r="1.5" fill="${pole}" stroke="#fff" stroke-width="1"/>`
    + `</svg>`;
}
export const flagDataUrl = (width: number, height: number) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(flagSvg(width, height))}`;

const DAY: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", year: "numeric" };
const TIME: Intl.DateTimeFormatOptions = { ...DAY, hour: "numeric", minute: "2-digit" };

/** Date-only strings are local calendar days, so parse them without a UTC shift. */
function day(s: string) {
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", DAY);
}

export function when(e: PennEvent): { range: string; note?: string } {
  if (e.start_ts) {
    const start = new Date(e.start_ts).toLocaleString("en-US", TIME);
    if (!e.end_ts) return { range: start, note: "Start time only; no end time is listed." };
    return { range: `${start} to ${new Date(e.end_ts).toLocaleString("en-US", TIME)}` };
  }
  if (e.start_date) {
    if (e.open_ended) return { range: `From ${day(e.start_date)}`, note: "No end date is listed." };
    const listing = LISTING_SOURCES.includes(e.source);
    if (listing && e.start_date === e.end_date) return { range: day(e.start_date), note: "No hours listed." };
    return { range: `${day(e.start_date)} to ${e.end_date ? day(e.end_date) : "an unlisted date"}`,
      note: listing ? "No hours listed." : "The permit lists dates only, not hours." };
  }
  return { range: "Dates not listed" };
}

/** Demo mode: a fixed "now" (NEXT_PUBLIC_PENN_NOW, ISO 8601), matching the API's PENN_NOW, so a frozen
 *  snapshot (jobs/freeze_demo.py) looks the same whenever the demo is recorded. */
export const PINNED_NOW = process.env.NEXT_PUBLIC_PENN_NOW || "";
export function appNow(): Date {
  return PINNED_NOW ? new Date(PINNED_NOW) : new Date();
}

function todayISO(now = appNow()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** Whether the permit is in effect today: drawn at full strength on the map, faded otherwise. */
export function activeToday(e: PennEvent, now = appNow()): boolean {
  // A start time with no end (a show, a game) happens on its start day: in effect all of that day.
  if (e.start_ts && !e.end_ts) return localISO(e.start_ts) === todayISO(now);
  if (e.start_ts) return new Date(e.start_ts) <= now && now <= new Date(e.end_ts!);
  if (e.start_date) {
    const t = todayISO(now);
    return e.start_date.slice(0, 10) <= t && (e.open_ended || !e.end_date || t <= e.end_date.slice(0, 10));
  }
  return false;
}

/** Calendar days between a local date (YYYY-MM-DD) and today; positive when the date is later. */
function dayDiff(iso: string, now: Date) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 864e5);
}
const localISO = (ts: string) => todayISO(new Date(ts));

/** Whether the record is over, by its own end: an end time that has passed (a 7:30 to 10:30 AM closure is over
 *  at 10:31), or an end date before today. A start time with no end lasts its day; an open-ended permit never ends. */
export function isOver(e: PennEvent, now = appNow()): boolean {
  if (e.end_ts) return new Date(e.end_ts) < now;
  if (e.start_ts) return localISO(e.start_ts) < todayISO(now);
  return !!e.end_date && !e.open_ended && e.end_date.slice(0, 10) < todayISO(now);
}

/** Days from today to the permit: 0 in effect today, days until it starts, or days since it ended. */
export function daysAway(e: PennEvent, now = appNow()): number {
  if (activeToday(e, now)) return 0;
  if (e.end_ts && localISO(e.end_ts) === todayISO(now) && new Date(e.end_ts) < now) return 0;   // over, earlier today
  const start = e.start_ts ? localISO(e.start_ts) : e.start_date;
  const end = e.end_ts ? localISO(e.end_ts) : e.end_date;
  if (start && dayDiff(start, now) > 0) return dayDiff(start, now);
  if (end && dayDiff(end, now) < 0) return -dayDiff(end, now);
  return FADE_DAYS;
}

const plural = (n: number) => `${n} day${n === 1 ? "" : "s"}`;

export function todayStatus(e: PennEvent, now = appNow()): string {
  if (e.start_ts && !e.end_ts && activeToday(e, now)) {
    return new Date(e.start_ts) > now ? `Today at ${clock(e.start_ts)}` : `Today; started at ${clock(e.start_ts)}`;
  }
  if (activeToday(e, now)) return "In effect today";
  if (e.end_ts && localISO(e.end_ts) === todayISO(now) && new Date(e.end_ts) < now) return `Ended today at ${clock(e.end_ts)}`;
  const start = e.start_ts ? localISO(e.start_ts) : e.start_date;
  const end = e.end_ts ? localISO(e.end_ts) : e.end_date;
  if (start && dayDiff(start, now) > 0) return `Starts in ${plural(dayDiff(start, now))}, on ${day(start)}`;
  if (end && dayDiff(end, now) < 0) return `Ended ${plural(-dayDiff(end, now))} ago, on ${day(end)}`;
  return "Not in effect today";
}

const SHORT_DAY: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" };
const CLOCK: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" };
const shortDay = (s: string) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString("en-US", SHORT_DAY); };
const clock = (ts: string) => new Date(ts).toLocaleTimeString("en-US", CLOCK).replace(":00", "");

/** One line for the hover card: "Sat, Oct 17, 7 AM", "Sat, Oct 17, 11 AM to 1 PM", "Thu, Oct 15 to Sat, Oct 17". */
export function shortWhen(e: PennEvent): string {
  if (e.start_ts) {
    const d = shortDay(localISO(e.start_ts));
    return e.end_ts ? `${d}, ${clock(e.start_ts)} to ${clock(e.end_ts)}` : `${d}, ${clock(e.start_ts)}`;
  }
  if (!e.start_date) return "Dates not listed";
  if (!e.end_date || e.end_date === e.start_date) return shortDay(e.start_date);
  return `${shortDay(e.start_date)} to ${shortDay(e.end_date)}`;
}

/** A listing's description is its "Where:" line, then any qualifier ("Sundays only"); drop the city and state. */
export function listingWhere(e: PennEvent): { where: string; note: string } {
  const [where = "", ...rest] = tidy(e.description || "").split(/;\s*/);
  return { where: where.replace(/,\s*Philadelphia,\s*PA$/i, ""), note: rest.join("; ") };
}

const TYPE: Record<string, string> = {
  utility_work: "Utility work", construction: "Construction", parking_restriction: "Parking restriction",
  block_party: "Block party", roadwork: "Roadwork", event: "Event", film_shoot: "Film shoot",
};
export const typeLabel = (t: string) => TYPE[t] ?? t.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());

/** Titles arrive in title case with stray whitespace; keep them, just tidy. */
export const tidy = (s: string) => s.replace(/\s+/g, " ").trim();

/** "1500 block of SPRUCE ST" -> "1500 block of Spruce St"; leaves mixed-case text alone. */
export const streetCase = (s: string) =>
  s.replace(/\b([A-Z])([A-Z']+)\b/g, (_, a: string, b: string) => a + b.toLowerCase()).replace(/\b(\d+)(ST|ND|RD|TH)\b/g, (_, n: string, x: string) => n + x.toLowerCase());

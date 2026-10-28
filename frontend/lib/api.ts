export const API = process.env.NEXT_PUBLIC_PENN_API ?? "http://localhost:8000";

export type PennEvent = {
  event_id: string; source: string; event_type: string; title: string; description: string;
  organization: string; occupancy: string; source_url: string; source_authority: string;
  claim_certainty: string; start_date: string | null; end_date: string | null;
  start_ts: string | null; end_ts: string | null; open_ended: boolean; status: string; geometry_wkt: string;
  centroid_lat?: number; centroid_lon?: number;   // /map only: what the legend's "in view" counts by
};

/** Center and bounds of a resolved place, as /locate and /agent return them; point is the exact
 *  corner or address (absent for a neighborhood). */
export type View = { center: [number, number]; bounds: [[number, number], [number, number]]; point?: [number, number] | null };

export type Located = { found: boolean; place: string | null; view?: View; window?: string };

/** One earlier exchange, sent back so PENN can read follow-ups ("what about next week?"). */
export type AskTurn = { question: string; answer: string };
/** The place and window the last answer used, as /agent returned it; sent back as is. */
export type AskContext = Record<string, unknown>;

export type AgentReply = {
  answer?: string; notes?: string[]; outside?: boolean; message?: string;
  asked_back?: boolean;          // nothing was searched: PENN asked where, or chatted; offer a pin
  context?: AskContext;
  place?: string | null; window?: string | null; window_kind?: "before_records" | "past" | "current" | "future" | null;
  start?: string; end?: string; view?: View | null; model?: string | null; tools?: string[]; cited?: string[];
  nearby?: string[];             // what the place search found at the place and the answer did not cite: lit too
  web?: { id: string; title: string; url: string; site: string }[];   // web pages the answer cites (p5-09)
  landmarks?: { name: string; point: [number, number]; bounds: [[number, number], [number, number]] }[];   // p3-10
  guard?: Guard | null;          // what the checks vouch for; null when there is no answer
  cached_at?: string;            // the public demo served a stored answer, first given at this time (ISO)
};

/** The hallucination guard behind an answer: records cited (each matched to this turn's tool results),
 * whether code resolved the place and dates, and every draft a check sent back before the answer. */
export type Guard = { cited: number; resolved_in_code: boolean; sent_back: { kind: string; label: string }[] };

export async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  // fetch rejects with a bare "Failed to fetch" when nothing answers; say which server is missing.
  const r = await fetch(`${API}${path}`, init).catch(() => { throw new Error(`the PENN API at ${API} is not reachable`); });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({})))?.detail ?? `Request failed (${r.status})`;
    // 401/404/413/429/503: the public demo's guardrails (penn/guardrails.py), whose message is meant to be shown
    // as is. 401: the judge code was refused, so the page forgets it and asks again.
    throw Object.assign(new Error(detail), { limited: [401, 404, 413, 429, 503].includes(r.status), badCode: r.status === 401 });
  }
  return r.json();
}

/** Several records in one request (POST /events), in the order asked; ids PENN has no record of are left out. */
export function getEvents(ids: string[]): Promise<PennEvent[]> {
  return ids.length ? getJSON<PennEvent[]>("/events", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }) }) : Promise.resolve([]);
}

// ---- the judge gate (components/RoleGate.tsx) ----
// The role is only what this page shows: the server checks the judge code on every question, so editing these
// or skipping the gate gets a visitor's allowance, never a judge's.
export type Role = "judge" | "visitor";
const ROLE_KEY = "penn.role", CODE_KEY = "penn.judgeCode";

export function savedRole(): { role: Role; code: string } | null {
  try {
    const role = localStorage.getItem(ROLE_KEY);
    if (role === "visitor") return { role, code: "" };
    const code = localStorage.getItem(CODE_KEY) ?? "";
    return role === "judge" && code ? { role, code } : null;
  } catch { return null; }   // storage blocked (private mode): the gate simply shows again
}

export function saveRole(role: Role, code = "") {
  try { localStorage.setItem(ROLE_KEY, role); if (code) localStorage.setItem(CODE_KEY, code); else localStorage.removeItem(CODE_KEY); }
  catch { /* the choice lasts this visit only */ }
}

export function forgetRole() {
  try { localStorage.removeItem(ROLE_KEY); localStorage.removeItem(CODE_KEY); } catch { /* nothing stored */ }
}

const judgeHeader = (): Record<string, string> => {
  const r = savedRole();
  return r?.role === "judge" ? { "X-PENN-Judge": r.code } : {};
};

/** Resolves when the server accepts the code; rejects with its message otherwise. */
export const verifyJudge = (code: string) =>
  getJSON<{ ok: boolean }>("/judge", { method: "POST", headers: { "X-PENN-Judge": code } });

export const locate = (q: string) => getJSON<Located>(`/locate?q=${encodeURIComponent(q)}`);

export const askAgent = (body: { question: string; lat?: number; lon?: number; history?: AskTurn[]; context?: AskContext | null }) =>
  getJSON<AgentReply>("/agent", { method: "POST", headers: { "Content-Type": "application/json", ...judgeHeader() }, body: JSON.stringify(body) });

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

/** Why a street is affected, which is what people ask: construction, an event, or a block party. No parking and
 *  the listings are their own kinds, since none of them closes a street. Color on the map. */
export type Category = "construction" | "event" | "blockparty" | "parking" | "listing" | "ticketed";
/** How much of the street a closure takes, as its permit lists it. Line weight on the map. */
export type Extent = "full" | "lane" | "sidewalk";
/** Listings mark something happening at a place, never a closure: visitphilly picks and Ticketmaster events. */
export const isListing = (c: Category) => c === "listing" || c === "ticketed";

/** Sources of events that happen at a place without closing a street: drawn as pins, never as closures. */
export const LISTING_SOURCES = ["visitphilly", "ticketmaster"];
export const LISTED_BY: Record<string, string> = { visitphilly: "visitphilly.com", ticketmaster: "Ticketmaster" };

/** The record's reason. Road, utility and building work are all construction; so is the odd other permit (a
 *  banner, an unmapped work type), since every one of them is work in the street. */
export function category(e: PennEvent): Category {
  if (e.source === "ticketmaster") return "ticketed";
  if (LISTING_SOURCES.includes(e.source)) return "listing";
  if (e.event_type === "event" || e.event_type === "film_shoot") return "event";
  if (e.event_type === "parking_restriction") return "parking";
  if (e.event_type === "block_party") return "blockparty";
  return "construction";
}

/** A permit's occupancy as a line weight. An announced event closure lists none: it closes the street. */
export function extent(e: PennEvent): Extent {
  if (e.occupancy === "Footway") return "sidewalk";
  if (e.occupancy === "Partial Closure") return "lane";
  return "full";
}

/** The reasons, then the kinds that close nothing: the order of the key. Construction is work-zone orange, the
 *  color of cones and "Road Work Ahead" signs; block parties are green, across the color wheel from it, so the
 *  two never blur on a thin line (rose did); no parking is a neutral grey, since it closes nothing and teal sat
 *  too close to the green. The listings that close nothing are ink pins (pinSvg). */
export const CATEGORIES: { key: Category; color: string; label: string; detail: string }[] = [
  { key: "construction", color: "#E35205", label: "Construction", detail: "Road, utility or building work permit" },
  { key: "event", color: "#5A3FC0", label: "Event closure", detail: "Announced by the City for an event" },
  { key: "blockparty", color: "#0E9F6E", label: "Block party", detail: "Block party permit" },
  { key: "parking", color: "#6B7280", label: "No parking", detail: "Parking or loading restriction" },
  { key: "listing", color: "#1B2433", label: "Event listing", detail: "visitphilly.com pick; no street closure listed" },
  { key: "ticketed", color: "#1B2433", label: "Ticketed event", detail: "Ticketmaster listing; no street closure listed" },
];
/** Plain-language meaning of each category, for the key's explanation. */
export const MEANING: Record<Category, string> = {
  construction: "Road work, utility work (gas, water, electric) or building work (dumpsters, sheds, cranes).",
  event: "The City announced the street closes for an event, such as a race or a parade.",
  blockparty: "A block party permit closes the block to traffic for the party.",
  parking: "Parking or loading is restricted at the curb.",
  listing: "An editor's pick on visitphilly.com: something happening at this place. A listing is not a street closure.",
  ticketed: "A ticketed event at this venue, listed by Ticketmaster. A listing is not a street closure.",
};
/** The line weights, heaviest first, with what each means for getting through. */
export const EXTENTS: { key: Extent; label: string; detail: string }[] = [
  { key: "full", label: "Full street", detail: "Closed to traffic" },
  { key: "lane", label: "One lane", detail: "A lane is closed; traffic still gets through" },
  { key: "sidewalk", label: "Sidewalk", detail: "Sidewalk only; the road stays open" },
];
/** The ground under the streets, set over Positron's greys: a light limestone city with one step darker for
 * buildings, so the white street grid is the brightest thing on the map and permit colors sit on paper, not on
 * a grey wash. Residential land is the plain ground (Positron greys it, which tints nearly every block). The
 * rivers get a real blue-green: the Schuylkill and the Delaware frame Center City. */
export const GROUND = { land: "#F4F5F1", building: "#E8E9E3", buildingEdge: "#DCDED6", water: "#BCD5DB", waterway: "#A9C8D0" };
/** Basemap road colors, set over Positron's (see page.tsx). Highways are the same white as streets, told apart by
 * their width and a firmer edge; a blue-grey highway competed with the event purple. Every other road class is
 * drawn as one plain street: OpenStreetMap's primary/secondary/tertiary/residential tags change block by block on
 * the same Philadelphia street, so a major-vs-local split looks random. */
export const ROAD = { highway: "#FFFFFF", highwayEdge: "#C2C7BF", street: "#FFFFFF", streetEdge: "#DADDD6" };
/** Parks and woods as a quiet sage: enough to find Rittenhouse Square or Fairmount Park, green enough to read as
 * a park, and soft enough that no permit color has to compete with it. */
export const PARK = { park: "#D3E2CC", wood: "#C8D9C0" };
/** Neighborhood outlines: slate, not green, so they read apart from the parks and from the sign-green outline of
 * the neighborhood an answer is about. */
export const HOOD = { line: "#56657D", label: "#46546B" };
/** Zoomed out, the map shows only what closes a whole street. One-lane and sidewalk permits and no-parking
 * curbs, three in four of the lines, wait for street zoom: drawn together across a neighborhood they bury the
 * closures people plan around, and at street level they are what someone parking or walking needs. */
export const MINOR: readonly Category[] = ["parking"];
export const MINOR_ZOOM = 15;
export const isMinor = (e: PennEvent) => {
  const c = category(e);
  return !isListing(c) && (MINOR.includes(c) || extent(e) !== "full");
};
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

/** The listing marker: an ink pin, white-edged so it reads over closure lines. Ink, not a reason's color: color on
 * the map says why a street closes, and a listing closes none. A star inside for a visitphilly pick, a ticket stub
 * for a Ticketmaster event. One drawing for the map icon and the key. The pin's tip (x 8, y 19.6 of 16 x 21) is the
 * event's spot. */
export function pinSvg(width: number, height: number, cat: Category = "listing"): string {
  const ink = COLOR[cat];
  const glyph = cat === "ticketed"
    ? `<path d="M4.2 5.7h7.6v1.3a1.1 1.1 0 0 0 0 2.2v1.3H4.2V9.2a1.1 1.1 0 0 0 0-2.2z" fill="#fff"/>`
      + `<line x1="9.5" y1="6" x2="9.5" y2="10.2" stroke="${ink}" stroke-width=".7" stroke-dasharray=".8 .7"/>`
    : `<path d="M8 4.1l1.15 2.35 2.6.38-1.88 1.83.44 2.58L8 10.02 5.69 11.24l.44-2.58L4.25 6.83l2.6-.38z" fill="#fff"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 16 21">`
    + `<path d="M8 19.6C8 19.6 1.2 12.9 1.2 8A6.8 6.8 0 0 1 14.8 8C14.8 12.9 8 19.6 8 19.6Z" fill="${ink}" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>`
    + glyph + `</svg>`;
}
export const pinDataUrl = (width: number, height: number, cat: Category = "listing") =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(pinSvg(width, height, cat))}`;

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
 *  snapshot (the penn_demo database) looks the same whenever the demo is recorded. */
export const PINNED_NOW = process.env.NEXT_PUBLIC_PENN_NOW || "";
/** The real "now" (or the pinned demo time): what "Updated today" and the preset windows count from. */
export function realNow(): Date {
  return PINNED_NOW ? new Date(PINNED_NOW) : new Date();
}

// Replay: the time slider moves the map's clock, and everything that asks "is this in effect now, coming up,
// or over" follows it: records fade in as their day nears, show at full strength while in effect, and drop
// off once over. The clock is a span, one step of the slider (a day): a 7:30 to 10:30 AM closure is in effect
// on its day, not only at the instant the step starts. null when the map is live.
let replayClock: number | null = null;
let replayStep = 0;
export function setReplayClock(at: number | null, stepMs = 0) { replayClock = at; replayStep = at === null ? 0 : stepMs; }
export function appNow(): Date {
  return replayClock !== null ? new Date(replayClock) : realNow();
}
/** The end of what the map shows: the replay step's end, or now itself when live. */
export function appUntil(): Date {
  return replayClock !== null ? new Date(replayClock + replayStep) : appNow();
}

/** /busiest: each day's listings, ticketed shows, event street closures and block parties (backend api.py). */
export type BusyDay = { day: string; listings: number; ticketed: number; closures: number; notices: number;
  block_parties: number; total: number };
export type Busiest = { days: BusyDay[]; busiest: string | null };

function todayISO(now = appNow()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** Whether the permit is in effect today (live), or at any time in the replay's step from `now` to `until`:
 *  drawn at full strength on the map, faded otherwise. */
export function activeToday(e: PennEvent, now = appNow(), until = appUntil()): boolean {
  const last = until > now ? new Date(until.getTime() - 1) : now;   // the step's last moment, inside it
  // A start time with no end (a show, a game) happens on its start day: in effect all of that day.
  if (e.start_ts && !e.end_ts) { const d = localISO(e.start_ts); return todayISO(now) <= d && d <= todayISO(last); }
  if (e.start_ts) return new Date(e.start_ts) <= last && now <= new Date(e.end_ts!);
  if (e.start_date) {
    return e.start_date.slice(0, 10) <= todayISO(last)
      && (e.open_ended || !e.end_date || todayISO(now) <= e.end_date.slice(0, 10));
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
export function daysAway(e: PennEvent, now = appNow(), until = appUntil()): number {
  if (activeToday(e, now, until)) return 0;
  if (e.end_ts && localISO(e.end_ts) === todayISO(now) && new Date(e.end_ts) < now) return 0;   // over, earlier today
  const start = e.start_ts ? localISO(e.start_ts) : e.start_date;
  const end = e.end_ts ? localISO(e.end_ts) : e.end_date;
  if (start && dayDiff(start, now) > 0) return dayDiff(start, now);
  if (end && dayDiff(end, now) < 0) return -dayDiff(end, now);
  return FADE_DAYS;
}

const plural = (n: number) => `${n} day${n === 1 ? "" : "s"}`;

const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
const count = (n: number, one: string, many: string) => `${n < 10 ? WORDS[n] : n} ${n === 1 ? one : many}`;
/** An event closure's event: "Philadelphia Marathon" of "Philadelphia Marathon: Benjamin Franklin Parkway". */
const eventName = (e: PennEvent) => tidy(e.title.split(":")[0]);

/** The answer's headline, from the records it cites, never from its prose: what is in effect during the answer's
 *  dates, biggest first, at most two kinds ("Two block parties", "Five street closures and three events").
 *  A record from outside those dates (a past year's closure cited as a pattern) does not count, so an answer
 *  that found nothing reads "Nothing on record". With no dates asked and one named event cited (its closures
 *  across past years), the headline is the event's name. An event's closure segments count once. */
export function answerHeadline(cited: PennEvent[], start?: string | null, end?: string | null): string {
  const first = start ? start.slice(0, 10) : null;
  const last = end ? todayISO(new Date(Date.parse(end) - 1)) : null;   // the window ends at a midnight it excludes
  const live = cited.filter(e => {
    if (!first || !last) return true;
    const s = e.start_ts ? localISO(e.start_ts) : e.start_date?.slice(0, 10);
    const t = e.end_ts ? localISO(e.end_ts) : e.open_ended ? "9999-12-31" : (e.end_date ?? e.start_date)?.slice(0, 10) ?? s;
    return !!s && s <= last && (t ?? s) >= first;
  });
  if (!live.length) return "Nothing on record";
  const names = new Set(live.filter(e => category(e) === "event").map(eventName));
  if (!first && names.size === 1 && live.every(e => category(e) === "event")) return [...names][0];
  const seen = new Set<string>(), kinds = new Set<string>();
  let closures = 0, events = 0, lanes = 0, walks = 0, parking = 0;
  for (const e of live) {
    const c = category(e);
    if (isListing(c)) events++;
    else if (c === "event") { if (!seen.has(eventName(e))) { seen.add(eventName(e)); closures++; kinds.add("event"); } }
    else if (c === "parking") parking++;
    else if (extent(e) === "sidewalk") walks++;
    else if (extent(e) === "lane") lanes++;
    else { closures++; kinds.add(c === "blockparty" ? "blockparty" : "work"); }
  }
  const only = (k: string) => kinds.size === 1 && kinds.has(k);
  const closure: [string, string] = only("blockparty") ? ["block party", "block parties"]
    : only("event") ? ["event closure", "event closures"] : ["street closure", "street closures"];
  // What it does to the street leads, biggest first; events come second ("One lane closure and two events"),
  // then the next kind of street work when there are none.
  const street: [number, string, string][] = [[closures, ...closure], [lanes, "lane closure", "lane closures"],
    [walks, "sidewalk closure", "sidewalk closures"], [parking, "no-parking zone", "no-parking zones"]];
  const work = street.filter(([n]) => n);
  const happening: [number, string, string][] = events ? [[events, "event", "events"]] : [];
  const line = [...work.slice(0, 1), ...happening, ...work.slice(1)].slice(0, 2)
    .map(([n, one, many]) => count(n, one, many)).join(" and ");
  return line.charAt(0).toUpperCase() + line.slice(1);
}

export function todayStatus(e: PennEvent, now = appNow(), until = appUntil()): string {
  if (e.start_ts && !e.end_ts && activeToday(e, now, until)) {
    return new Date(e.start_ts) > now ? `Today at ${clock(e.start_ts)}` : `Today; started at ${clock(e.start_ts)}`;
  }
  if (activeToday(e, now, until)) return "In effect today";
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

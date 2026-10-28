import { useEffect, useRef } from "react";
import {
  activeToday, category, daysAway, EXTENTS, extent, isListing, shortWhen, DETAIL, fade, LABEL, LISTED_BY, listingWhere, streetCase, tidy, todayStatus, typeLabel, when, type PennEvent,
} from "@/lib/api";
import { Swatch } from "./Legend";

type Props = {
  open: boolean;
  /** Several records under one click: the panel lists them first. */
  choices: PennEvent[];
  event: PennEvent | null;
  loading: boolean;
  error: string;
  citedNumber: (id: string) => number;
  /** The answer's citations the list came from ("6–16"), or null when it is the records under one click. */
  range: [number, number] | null;
  /** The same event's other listings (a visitphilly pick and its Ticketmaster dates). */
  partnersOf: (e: PennEvent) => PennEvent[];
  /** Every date of the same show at the same venue (just the event itself when it has one). */
  datesOf: (e: PennEvent) => PennEvent[];
  onPick: (e: PennEvent) => void;
  onBack: (() => void) | null;
  onClose: () => void;
};

export function PermitPanel({ open, choices, event, loading, error, citedNumber, range, partnersOf, datesOf, onPick, onBack, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  // A closed panel stays in the DOM for its slide; inert keeps it out of the tab order.
  useEffect(() => { panelRef.current?.toggleAttribute("inert", !open); }, [open]);
  useEffect(() => { if (open) closeRef.current?.focus({ preventScroll: true }); }, [open, event?.event_id, choices.length]);

  return (
    <aside ref={panelRef} className={`panel${open ? " open" : ""}`} aria-label="Permit details">
      <div className="panel-bar">
        {onBack && event
          ? <button type="button" className="text-btn" onClick={onBack}>All {choices.length} {range ? "cited" : "here"}</button>
          : <span className="panel-kicker">{event ? (isListing(category(event)) ? "Event" : "Permit")
            : range ? `Records ${range[0]}–${range[1]} in the answer` : `${choices.length} records on this spot`}</span>}
        <button ref={closeRef} type="button" className="icon-btn" aria-label="Close details (Esc)" onClick={onClose}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 3.5l9 9m0-9l-9 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
        </button>
      </div>

      {event ? (isListing(category(event))
          ? <ListingDetail e={event} loading={loading} error={error} cited={citedNumber(event.event_id)} partners={partnersOf(event)} dates={datesOf(event)} />
          : <Detail e={event} loading={loading} error={error} cited={citedNumber(event.event_id)} />)
        : (
          <ul className="choices">
            {choices.map(c => {
              const cat = category(c);
              return (
                <li key={c.event_id}>
                  <button type="button" onClick={() => onPick(c)}>
                    <Swatch cat={cat} ext={extent(c)} opacity={fade(daysAway(c))} />
                    <span>
                      <span className="choice-cat">{LABEL[cat]}</span>
                      <span className="choice-title">{tidy(c.title)}</span>
                      <span className="choice-when">{when(c).range}. {todayStatus(c)}.</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
    </aside>
  );
}

function Detail({ e, loading, error, cited }: { e: PennEvent; loading: boolean; error: string; cited: number }) {
  const cat = category(e);
  const w = when(e);
  const where = streetCase(tidy(e.description || ""));
  // Renewals append "Extend From Permit 2026-06241" to the title, sometimes more than once.
  const extends_ = Array.from(tidy(e.title).matchAll(/extend(?:ed)? from permit\s*#?\s*([\d-]+)/gi), m => m[1]);
  const title = tidy(e.title).replace(/\s*extend(?:ed)? from permit\s*#?\s*[\d-]+/gi, "").trim();
  return (
    <div className="detail">
      <div className={`cat-tag cat-${cat}`}><Swatch cat={cat} ext={extent(e)} /> {LABEL[cat]}</div>
      <h2>{title || typeLabel(e.event_type)}</h2>
      {extends_.length > 0 && <p className="sub extends">Extends permit {extends_.join(" and ")}</p>}
      {cited > 0 && <p className="cited-flag"><span className="cite static">{cited}</span> Cited in the answer</p>}

      <dl>
        <div><dt>Where</dt><dd>{where ? where.split(/;\s*/).map((s, i) => <span key={i} className="where-line">{s}</span>) : "Location not described"}</dd></div>
        <div><dt>When</dt><dd>{w.range}{w.note && <span className="sub">{w.note}</span>}
          <span className={`today${activeToday(e) ? " on" : ""}`}>{todayStatus(e)}.</span></dd></div>
        {/* How much of the street, in plain words, with the permit's own term under it. */}
        <div><dt>Closure</dt><dd>{cat === "event" || cat === "parking" ? DETAIL[cat] : EXTENTS.find(x => x.key === extent(e))!.detail}
          {cat !== "event" && e.occupancy && <span className="sub">{e.occupancy}</span>}</dd></div>
        <div><dt>Type</dt><dd>{typeLabel(e.event_type)}</dd></div>
        <div><dt>Who</dt><dd>{e.organization || "Not listed"}</dd></div>
        <div><dt>Record</dt><dd>
          <span className="record-id">{e.event_id.split(":").slice(1).join(":")}</span>
          <span className="sub">{sourceName(e.source)}, {e.status}{e.claim_certainty ? `, ${e.claim_certainty}` : ""}</span>
        </dd></div>
      </dl>

      {loading && <p className="sub" role="status">Updating from the City record</p>}
      {error && <p className="panel-error" role="alert">{error}</p>}
      {e.source_url && <a className="source-link" href={e.source_url} target="_blank" rel="noreferrer">Open the source dataset</a>}
      <p className="caveat">A permit means the City allowed the work in this window. It does not confirm the lane is closed right now.</p>
    </div>
  );
}

const MAX_DATES = 12;

/** An event listed by a guide or ticket seller (visitphilly, Ticketmaster): no permit, no closure; what, when, where, and the event's own page. */
function ListingDetail({ e, loading, error, cited, partners, dates }:
    { e: PennEvent; loading: boolean; error: string; cited: number; partners: PennEvent[]; dates: PennEvent[] }) {
  const w = when(e);
  const { where, note } = listingWhere(e);
  const lister = LISTED_BY[e.source] ?? e.source;
  // A local pick's organization reads "visitphilly.com, updated <date>": show the guide's description, keep the date.
  const listedBy = e.source === "visitphilly" && e.organization?.startsWith("visitphilly.com")
    ? lister[0].toUpperCase() + lister.slice(1) + e.organization.slice("visitphilly.com".length) : e.organization || lister;
  return (
    <div className="detail">
      <div className={`cat-tag cat-${category(e)}`}><Swatch cat={category(e)} /> {LABEL[category(e)]}</div>
      <h2>{tidy(e.title)}</h2>
      {cited > 0 && <p className="cited-flag"><span className="cite static">{cited}</span> Cited in the answer</p>}

      <dl>
        <div><dt>When</dt><dd>{w.range}{w.note && <span className="sub">{w.note}</span>}{note && <span className="sub">{note}</span>}
          <span className={`today${activeToday(e) ? " on" : ""}`}>{todayStatus(e)}.</span></dd></div>
        {dates.length > 1 && (
          <div><dt>Dates</dt><dd>
            <ul className="run-dates">
              {dates.slice(0, MAX_DATES).map(d => (
                <li key={d.event_id} className={d.event_id === e.event_id ? "this" : ""}>
                  {d.source_url && d.source !== "visitphilly"
                    ? <a href={d.source_url} target="_blank" rel="noreferrer">{shortWhen(d)}</a> : shortWhen(d)}
                </li>
              ))}
            </ul>
            {dates.length > MAX_DATES && <span className="sub">and {dates.length - MAX_DATES} more in this window</span>}
          </dd></div>
        )}
        <div><dt>Where</dt><dd>{where || "Location not described"}</dd></div>
        <div><dt>Streets</dt><dd>No street closure listed<span className="sub">Closures for this event, if any, show as their own lines.</span></dd></div>
        <div><dt>Listed by</dt><dd>{listedBy}
          {partners.length > 0 && <span className="sub">
            Also {partners[0].source === "visitphilly" ? "a local pick in " : "sold on "}
            <a href={partners[0].source_url} target="_blank" rel="noreferrer">{LISTED_BY[partners[0].source] ?? partners[0].source}</a>
            {partners.length > 1 && ` (${partners.length} dates)`}
          </span>}</dd></div>
        <div><dt>Record</dt><dd><span className="record-id">{e.event_id.split(":").slice(1).join(":")}</span></dd></div>
      </dl>

      {loading && <p className="sub" role="status">Updating the listing</p>}
      {error && <p className="panel-error" role="alert">{error}</p>}
      {e.source_url && <a className="source-link" href={e.source_url} target="_blank" rel="noreferrer">Open the event page</a>}
      <p className="caveat">Listed by {lister}, not the City. A listing says what is happening here; it does not mean a street is closed.</p>
    </div>
  );
}

function sourceName(s: string) {
  return ({ lane_closures: "Streets Department permit", event_closures: "City event closure notice" } as Record<string, string>)[s] ?? s;
}

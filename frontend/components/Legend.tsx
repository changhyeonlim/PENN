import { useEffect, useState } from "react";
import { useSmoothSize } from "@/lib/motion";
import { CATEGORIES, COLOR, EXTENTS, pinDataUrl, HIGHLIGHT, HOOD, isListing, MEANING, MINOR, PARK, ROAD, STRENGTH, type Category, type Extent } from "@/lib/api";

// The weight samples are drawn in a neutral ink: the weight means the same in every reason's color.
const WEIGHT_INK = "#5A6474";
const SHORT: Record<Extent, string> = { full: "Closed", lane: "1 lane", sidewalk: "Sidewalk" };

/** A small drawing of each line style, matching the map layers: the reason's color at the record's weight.
 *  `color` overrides the reason's color, for the key's weight samples, which apply to every reason. */
export function Swatch({ cat, ext = "full", size = 28, opacity = 1, color }:
  { cat: Category | "cited"; ext?: Extent; size?: number; opacity?: number; color?: string }) {
  if (cat !== "cited" && isListing(cat)) {   // the map's pin marker at its map size (smaller blurs the glyph), centred in the swatch box
    return (
      // Opacity inline only when asked for, so a switched-off key row can still fade it from the stylesheet.
      <span className="swatch swatch-pin" style={{ width: size, ...(opacity !== 1 && { opacity }) }} aria-hidden="true">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={pinDataUrl(16, 21, cat)} width={16} height={21} alt="" />
      </span>
    );
  }
  const ink = color ?? (cat === "cited" ? COLOR.construction : COLOR[cat]);
  const y = 7;
  return (
    <svg className="swatch" width={size} height="14" viewBox={`0 0 ${size} 14`} aria-hidden="true" opacity={opacity}>
      {cat === "cited" && <line x1="3" y1={y} x2={size - 3} y2={y} stroke={HIGHLIGHT} strokeWidth="10" strokeLinecap="round" />}
      {cat === "cited" && <line x1="3" y1={y} x2={size - 3} y2={y} stroke={ink} strokeWidth="3" />}
      {cat === "parking" && <>
        <line x1="2" y1={y - 2.5} x2={size - 2} y2={y - 2.5} stroke={ink} strokeWidth="1.4" />
        <line x1="2" y1={y + 2.5} x2={size - 2} y2={y + 2.5} stroke={ink} strokeWidth="1.4" />
      </>}
      {cat !== "cited" && cat !== "parking" && ext === "full" && <>
        <line x1="1" y1={y} x2={size - 1} y2={y} stroke="#fff" strokeWidth="9" />
        <line x1="2" y1={y} x2={size - 2} y2={y} stroke={ink} strokeWidth="6" />
      </>}
      {cat !== "cited" && cat !== "parking" && ext === "lane" && <line x1="2" y1={y} x2={size - 2} y2={y} stroke={ink} strokeWidth="2.6" />}
      {cat !== "cited" && cat !== "parking" && ext === "sidewalk" &&
        <line x1="3" y1={y} x2={size - 2} y2={y} stroke={ink} strokeWidth="2.6" strokeLinecap="round" strokeDasharray="0.1 5" />}
    </svg>
  );
}

/** Basemap road classes, drawn the way the map draws them. */
function Road({ fill, edge, width }: { fill: string; edge: string; width: number }) {
  return (
    <svg className="swatch" width="28" height="14" viewBox="0 0 28 14" aria-hidden="true">
      <rect x="0" y="0" width="28" height="14" rx="3" fill="#F2F3F0" />
      <line x1="0" y1="7" x2="28" y2="7" stroke={edge} strokeWidth={width + 2} />
      <line x1="0" y1="7" x2="28" y2="7" stroke={fill} strokeWidth={width} />
    </svg>
  );
}

type Props = {
  counts: Partial<Record<Category, number>>; bands: number[]; citedCount: number; loading: boolean;
  hoods: boolean; onHoods: (on: boolean) => void;
  hidden: ReadonlySet<Category>; onHidden: (next: ReadonlySet<Category>) => void;
  /** Zoomed out past MINOR_ZOOM, where no-parking, one-lane and sidewalk lines are not drawn. */
  zoomedOut: boolean;
};

export function Legend({ counts, bands, citedCount, loading, hoods, onHoods, hidden, onHidden, zoomedOut }: Props) {
  const [open, setOpen] = useState(true);
  const [explain, setExplain] = useState(false);
  // Opening, folding and explaining change the key's height; it grows and shrinks there instead of jumping.
  const [box, setBox] = useState<HTMLElement | null>(null);
  useSmoothSize(box);
  // On a phone the map needs the room; the key starts folded.
  useEffect(() => { if (window.matchMedia("(max-width: 720px)").matches) setOpen(false); }, []);
  // A minor kind waits for the user to zoom in; its row says so instead of counting lines that are not drawn.
  const waiting = (cat: Category) => zoomedOut && MINOR.includes(cat);
  // "In view" counts what is drawn; a hidden row keeps its own count so you can see what you turned off.
  const total = CATEGORIES.reduce((n, c) => n + (hidden.has(c.key) || waiting(c.key) ? 0 : counts[c.key] ?? 0), 0);
  const toggle = (cat: Category) => {
    const next = new Set(hidden);
    if (!next.delete(cat)) next.add(cat);
    onHidden(next);
  };
  // "Only" shows one kind and hides the rest; on the kind already shown alone, it brings the others back.
  const solo = (cat: Category) => CATEGORIES.every(c => hidden.has(c.key) === (c.key !== cat));
  const only = (cat: Category) => onHidden(solo(cat) ? new Set() : new Set(CATEGORIES.map(c => c.key).filter(k => k !== cat)));
  return (
    <section ref={setBox} className={`legend${open ? "" : " closed"}${open && explain ? " explaining" : ""}`} aria-label="Map key">
      <button type="button" className="legend-head" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span>Map key</span>
        <span className="legend-total">{loading ? "Loading" : `${total} in view`}</span>
      </button>
      {open && (
        <>
          <ul>
            {/* Each kind's row is its own show/hide button: pressed is shown. */}
            {CATEGORIES.map(c => {
              const off = hidden.has(c.key);
              return (
                <li key={c.key} className={`legend-cat${counts[c.key] ? "" : " none"}${off ? " off" : ""}${waiting(c.key) ? " waiting" : ""}`}>
                  <div className="legend-line">
                    <button type="button" className="legend-row" aria-pressed={!off} onClick={() => toggle(c.key)}
                      title={`${off ? "Show" : "Hide"} ${c.label.toLowerCase()} on the map`}>
                      <Swatch cat={c.key} />
                      <span className="legend-label">{c.label}</span>
                      {waiting(c.key)
                        ? <span className="legend-count legend-zoom" title="Drawn once you zoom in to neighborhood scale">zoom in</span>
                        : <span className="legend-count">{counts[c.key] ?? 0}</span>}
                    </button>
                    <button type="button" className="legend-only" onClick={() => only(c.key)}
                      aria-label={solo(c.key) ? "Show every kind again" : `Show only ${c.label.toLowerCase()}`}>
                      {solo(c.key) ? "All" : "Only"}
                    </button>
                  </div>
                  {explain && <span className="legend-meaning">{MEANING[c.key]}</span>}
                </li>
              );
            })}
            {citedCount > 0 && (
              <li className="legend-cited">
                <Swatch cat="cited" />
                <span className="legend-label">In the answer</span>
                <span className="legend-count">{citedCount}</span>
                {explain && <span className="legend-meaning">Yellow marks the permits the answer cites. Numbers in the answer open them.</span>}
              </li>
            )}
          </ul>

          {/* How the weights read, always in view: how much of the street a closure of any reason takes. */}
          <div className="legend-weights" title={EXTENTS.map(x => `${x.label}: ${x.detail.toLowerCase()}`).join(". ") + "."
            + (zoomedOut ? " One-lane and sidewalk lines show once you zoom in to street level." : "")}>
            {EXTENTS.map(x => (
              <span key={x.key} className={`legend-weight${zoomedOut && x.key !== "full" ? " waiting" : ""}`}>
                <Swatch cat="construction" ext={x.key} size={16} color={WEIGHT_INK} />{SHORT[x.key]}
              </span>
            ))}
          </div>

          {/* How the fading reads, always in view: a line's strength is how soon it starts. */}
          <div className="legend-time"
            title={`Full strength: ${STRENGTH[0].label.toLowerCase()}. Fainter: ${STRENGTH.slice(1).map(b => b.label).join(", then ")}.`}>
            <span className="legend-time-label">Today</span>
            <span className="legend-time-ramp" aria-hidden="true">
              {STRENGTH.map(b => <Swatch key={b.from} cat="construction" size={16} opacity={b.opacity} />)}
            </span>
            <span className="legend-time-label">Coming up</span>
          </div>

          <div className={`legend-hoods${hoods ? "" : " off"}`}>
            <button type="button" className="legend-row" aria-pressed={hoods} onClick={() => onHoods(!hoods)}
              title={`${hoods ? "Hide" : "Show"} neighborhood outlines`}>
              <svg className="swatch" width="28" height="14" viewBox="0 0 28 14" aria-hidden="true">
                <line x1="1" y1="7" x2="27" y2="7" stroke={HOOD.line} strokeWidth="7" strokeOpacity="0.35" />
              </svg>
              <span className="legend-label">Neighborhoods</span>
            </button>
          </div>
          {explain && hoods && (
            <p className="legend-note">Outlines are 159 Philadelphia neighborhoods by Robert Cheetham (OpenDataPhilly,
              CC BY 4.0). A neighborhood you ask about is outlined in green.</p>
          )}

          {explain && (
            <div className="legend-explain">
              <p className="legend-sub">Line weight: how much of the street</p>
              <ul>
                {EXTENTS.map(x => (
                  <li key={x.key}><Swatch cat="construction" ext={x.key} color={WEIGHT_INK} /><span className="legend-label">{x.label}</span>
                    <span className="legend-meaning">{x.detail}</span></li>
                ))}
              </ul>
              <p className="legend-sub">Strength: how close to today</p>
              <ul>
                {STRENGTH.map((b, i) => (
                  <li key={b.from}><Swatch cat="construction" opacity={b.opacity} /><span className="legend-label">{b.label}</span>
                    <span className="legend-count">{bands[i] ?? 0}</span></li>
                ))}
              </ul>
              <p className="legend-sub">Roads</p>
              <ul>
                <li><Road fill={ROAD.highway} edge={ROAD.highwayEdge} width={7} /><span className="legend-label">Highway</span></li>
                <li><Road fill={ROAD.street} edge={ROAD.streetEdge} width={5} /><span className="legend-label">Street</span></li>
                <li>
                  <svg className="swatch" width="28" height="14" viewBox="0 0 28 14" aria-hidden="true">
                    <rect x="0" y="0" width="28" height="14" rx="3" fill={PARK.park} />
                  </svg>
                  <span className="legend-label">Park in the city</span>
                </li>
              </ul>
              <p className="legend-note">A permit means the City allowed the work for these dates. It does not confirm the street is closed at this moment.</p>
            </div>
          )}

          {/* The footer row is always there, so "Show all" appearing never changes the key's height. */}
          <div className="legend-foot">
            <button type="button" className="legend-more" aria-expanded={explain} onClick={() => setExplain(x => !x)}>
              {explain ? "Hide explanations" : "What do these mean?"}
            </button>
            {hidden.size > 0 && (
              <button type="button" className="legend-reset" onClick={() => onHidden(new Set())}
                aria-label={`Show all kinds again (${hidden.size} hidden)`}>Show all</button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

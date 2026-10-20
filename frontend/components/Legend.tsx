import { useEffect, useState } from "react";
import { CATEGORIES, COLOR, EXTENTS, flagDataUrl, HIGHLIGHT, HOOD, isListing, MEANING, MINOR, PARK, ROAD, STRENGTH, type Category, type Extent } from "@/lib/api";

// The weight samples are drawn in a neutral ink: the weight means the same in every reason's color.
const WEIGHT_INK = "#5A6474";
const SHORT: Record<Extent, string> = { full: "Closed", lane: "1 lane", sidewalk: "Sidewalk" };

/** A small drawing of each line style, matching the map layers: the reason's color at the record's weight.
 *  `color` overrides the reason's color, for the key's weight samples, which apply to every reason. */
export function Swatch({ cat, ext = "full", size = 28, opacity = 1, color }:
  { cat: Category | "cited"; ext?: Extent; size?: number; opacity?: number; color?: string }) {
  if (cat !== "cited" && isListing(cat)) {   // the map's flag marker, centred in the swatch box
    return (
      // Opacity inline only when asked for, so a switched-off key row can still fade it from the stylesheet.
      <span className="swatch swatch-flag" style={{ width: size, ...(opacity !== 1 && { opacity }) }} aria-hidden="true">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={flagDataUrl(13, 16)} width={13} height={16} alt="" />
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
};

export function Legend({ counts, bands, citedCount, loading, hoods, onHoods }: Props) {
  const [open, setOpen] = useState(true);
  const [explain, setExplain] = useState(false);
  // On a phone the map needs the room; the key starts folded.
  useEffect(() => { if (window.matchMedia("(max-width: 720px)").matches) setOpen(false); }, []);
  const total = CATEGORIES.reduce((n, c) => n + (counts[c.key] ?? 0), 0);
  return (
    <section className={`legend${open ? "" : " closed"}${open && explain ? " explaining" : ""}`} aria-label="Map key">
      <button type="button" className="legend-head" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span>Map key</span>
        <span className="legend-total">{loading ? "Loading" : `${total} in view`}</span>
      </button>
      {open && (
        <>
          <ul>
            {CATEGORIES.map(c => (
              <li key={c.key} className={counts[c.key] ? "" : "none"}>
                <Swatch cat={c.key} />
                <span className="legend-label">{c.label}</span>
                <span className="legend-count">{counts[c.key] ?? 0}</span>
                {explain && <span className="legend-meaning">{MEANING[c.key]}</span>}
              </li>
            ))}
            {citedCount > 0 && (
              <li className="legend-cited">
                <Swatch cat="cited" />
                <span className="legend-label">In the answer</span>
                <span className="legend-count">{citedCount}</span>
                {explain && <span className="legend-meaning">Yellow marks the permits the answer cites. Numbers in the answer open them.</span>}
              </li>
            )}
          </ul>

          <label className="legend-toggle">
            <input type="checkbox" checked={hoods} onChange={e => onHoods(e.target.checked)} />
            <svg className="swatch" width="28" height="14" viewBox="0 0 28 14" aria-hidden="true">
              <line x1="1" y1="7" x2="27" y2="7" stroke="#1E5B44" strokeWidth="8" strokeOpacity="0.2" />
            </svg>
            <span className="legend-label">Neighborhoods</span>
          </label>
          {explain && hoods && (
            <p className="legend-note">Outlines are the City&rsquo;s 159 neighborhoods (OpenDataPhilly). A neighborhood you ask
              about is outlined in green.</p>
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
                <li><Road fill={ROAD.street} edge="#E1E3E6" width={5} /><span className="legend-label">Street</span></li>
              </ul>
              <p className="legend-note">A permit means the City allowed the work for these dates. It does not confirm the street is closed at this moment.</p>
            </div>
          )}

          <button type="button" className="legend-more" aria-expanded={explain} onClick={() => setExplain(x => !x)}>
            {explain ? "Hide explanations" : "What do these mean?"}
          </button>
        </>
      )}
    </section>
  );
}

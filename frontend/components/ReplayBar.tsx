import type { BusyDay } from "@/lib/api";

const DAY = 24 * 3600e3;
const NY = "America/New_York";

/** "Sat, Oct 17" for a day's step, "Sat, Oct 17, 8 AM – 4 PM" for a shorter one, in Philadelphia time. */
export function replayStamp(at: number, step: number): string {
  const day = new Date(at).toLocaleDateString("en-US", { timeZone: NY, weekday: "short", month: "short", day: "numeric" });
  if (step >= DAY) return day;
  const hour = (t: number) => new Date(t).toLocaleTimeString("en-US", { timeZone: NY, hour: "numeric" });
  return `${day}, ${hour(at)} – ${hour(at + step)}`;
}

const dayLabel = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString("en-US",
  { weekday: "short", month: "short", day: "numeric" });
const monthDay = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
/** Whole days from one YYYY-MM-DD to another. */
const daysBetween = (a: string, b: string) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / DAY);

type Props = {
  start: number; end: number;            // the replay's span, ms
  step: number;                          // one move of the slider, ms (a day)
  days: BusyDay[]; busiest: string | null;
  at: number; onAt: (at: number) => void;
  loading: boolean;                      // the month's records are still on their way
  playing: boolean; onPlay: (on: boolean) => void;
  inEffect: number;                      // listings, ticketed shows, event closures and block parties in effect at `at`
  onBusiest: () => void;
  leaving?: boolean;                     // fading out after Replay is switched off
};

/** The replay's time slider. The month's days are the track, each bar as tall as that day is busy, and the
 *  handle is a needle through them: drag it, step it with the arrow keys, or play it, and the map's clock
 *  follows, so records fade in, take the street and drop off. */
export function ReplayBar({ start, end, step, days, busiest, at, onAt, loading, playing, onPlay, inEffect, onBusiest, leaving }: Props) {
  const first = new Date(start).toLocaleDateString("en-CA", { timeZone: NY });
  const current = new Date(at).toLocaleDateString("en-CA", { timeZone: NY });
  const count = Math.round((end - start) / DAY);
  const max = Math.max(1, ...days.map(d => d.total));
  // Where a moment sits on the track, 0 to 100, as the needle places it (it stops at the last step's start).
  const travel = Math.max(1, end - step - start);
  const x = (t: number) => (t - start) / travel * 100;
  const dayX = (i: number) => x(start + i * DAY + (DAY - step) / 2);
  const barW = 100 * DAY / travel * 0.62;
  const weeks = Array.from({ length: Math.ceil(count / 7) }, (_, w) => w * 7);
  const top = days.find(d => d.day === busiest);
  const unit = step >= DAY ? "that day" : "then";
  return (
    <section className={`replay${leaving ? " leaving" : ""}`} aria-label="Replay: move the map through the month">
      <div className="replay-head">
        <button type="button" className="replay-play" aria-label={playing ? "Pause" : step >= DAY ? "Play day by day" : "Play"}
          disabled={loading} onClick={() => onPlay(!playing)}>
          {playing
            ? <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3.5 2v8M8.5 2v8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>
            : <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3.4 1.6v8.8L10.4 6z" fill="currentColor" /></svg>}
        </button>
        <span className={`replay-time${loading ? " pending" : ""}`}>
          {loading ? new Date(start + DAY / 2).toLocaleDateString("en-US", { timeZone: NY, month: "long", year: "numeric" })
            : replayStamp(at, step)}
        </span>
        <span className="replay-count" role="status">
          {loading ? "Loading the month’s records" : <><b>{inEffect}</b> events, closures and block parties {unit}</>}
        </span>
        {!loading && top && top.day !== current && (
          <button type="button" className="text-btn replay-busiest" onClick={onBusiest}
            title={`${top.listings} listings, ${top.ticketed} ticketed shows, ${top.closures} event street closures `
              + `(${top.notices} City notices) and ${top.block_parties} block parties`}>
            Busiest: {dayLabel(top.day)}
          </button>
        )}
        {!loading && top && top.day === current && <span className="replay-busiest is-here">The month&rsquo;s busiest day</span>}
      </div>
      <div className="replay-track">
        <svg className="replay-bars" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
          {days.map(d => {
            const i = daysBetween(first, d.day);
            const h = Math.max(1.2, d.total / max * 28);
            const cls = d.day === current ? "now" : d.day === busiest ? "top" : d.day < current ? "past" : "";
            return <rect key={d.day} className={cls} x={dayX(i) - barW / 2} width={barW} y={30 - h} height={h} />;
          })}
        </svg>
        <input type="range" min={start} max={end - step} step={step} value={at} disabled={loading}
          aria-label="Time on the map" aria-valuetext={replayStamp(at, step)}
          onChange={e => { onPlay(false); onAt(Number(e.target.value)); }} />
      </div>
      <div className="replay-weeks" aria-hidden="true">
        {weeks.map(i => {
          const day = new Date(start + i * DAY + DAY / 2).toLocaleDateString("en-CA", { timeZone: NY });
          return <span key={i} style={{ left: `${dayX(i)}%` }}>{monthDay(day)}</span>;
        })}
      </div>
    </section>
  );
}

import { Fragment, type ReactNode } from "react";

// The citation forms the API accepts (see backend/penn/agent/citations.py).
const CITE = /[\[【]\s*(?:\{\s*"(?:event_)?id"\s*:\s*")?(?:[iI][dD][sS]?:\s*)?([a-z0-9_]+:[A-Za-z0-9_:\-.]+)"?\s*\}?\s*[\]】]/g;
// Several IDs in one bracket, 【ids:a:1, a:2】: split into one citation each, as split_multi does. A bare ID
// after the first ("lane_closures:2026-1, 2026-2") takes the source before it.
const ONE = String.raw`(?:[iI][dD]:\s*)?[a-z0-9_]+:[A-Za-z0-9_:\-.]+`;
const NEXT = String.raw`(?:[iI][dD]:\s*)?[A-Za-z0-9_][A-Za-z0-9_:\-.]*`;
const MULTI = new RegExp(String.raw`[\[【]\s*(?:[iI][dD][sS]?\s*:\s*)?(${ONE}(?:\s*[,;]\s*${NEXT})+)\s*[\]】]`, "g");

function oneEach(_: string, ids: string): string {
  let source = "";
  return ids.split(/\s*[,;]\s*/).map(raw => {
    const id = raw.replace(/^id:\s*/i, "");
    if (id.includes(":")) { source = id.split(":")[0]; return `[id:${id}]`; }
    return `[id:${source}:${id}]`;
  }).join(" ");
}

/** A web page an answer cites (p5-09): shown as a chip naming its site, never as a numbered record. */
export type WebSource = { id: string; title: string; url: string; site: string };
const isWeb = (id: string) => id.startsWith("web:");

/** Record citation ids in order of first appearance, so chip numbers match across the answer. Web pages
 *  are not records: they get site chips, not numbers. */
export function citationOrder(text: string): string[] {
  const order: string[] = [];
  for (const m of clean(text).matchAll(CITE)) if (!isWeb(m[1]) && !order.includes(m[1])) order.push(m[1]);
  return order;
}

function clean(text: string) {
  return text
    .replace(MULTI, oneEach)                                                                       // 【ids:a, b】: one chip each
    .replace(/\b(?:lane_closures|event_closures|philly_311|visitphilly|ticketmaster):[A-Za-z0-9_:\-.]+\s*(?=[\[【])/g, "")  // an ID written out right before its own citation
    .replace(/\[id:\s*none[^\]]*\]/gi, "")                                                        // "[id:none ...]" is not a citation
    .replace(/【\s*((?:\[[^\]]+\]\s*)+)】/g, "$1")                                                  // 【[id:a] [id:b]】: keep the inner cites
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, "$1")                                              // a markdown link: its text (the page is a chip)
    .trim();
}

type Props = {
  text: string; titles: Record<string, string>; selected: string | null; onCite: (id: string) => void;
  web?: WebSource[];
};

/** The model's light markdown (paragraphs, "- " lists, **bold**) with each citation as a numbered chip. */
export function AnswerText({ text, titles, selected, onCite, web = [] }: Props) {
  const body = clean(text);
  const order = citationOrder(text);
  const pages = Object.fromEntries(web.map(w => [w.id, w]));
  const citedWeb: WebSource[] = [];

  const inline = (s: string, key: string): ReactNode[] => {
    const out: ReactNode[] = [];
    let last = 0, i = 0;
    for (const m of s.matchAll(CITE)) {
      out.push(...bold(s.slice(last, m.index), `${key}t${i}`));
      const id = m[1];
      if (isWeb(id)) {
        const w = pages[id];
        if (w) {
          if (!citedWeb.includes(w)) citedWeb.push(w);
          out.push(
            <a key={`${key}w${i}`} className="web-cite" href={w.url} target="_blank" rel="noopener noreferrer"
              title={`${w.title} · opens ${w.site}`} aria-label={`Web page: ${w.title}, ${w.site} (opens in a new tab)`}>
              <Globe />{w.site}<External />
            </a>);
        }
        last = (m.index ?? 0) + m[0].length;
        i++;
        continue;
      }
      const n = order.indexOf(id) + 1;
      out.push(
        <button key={`${key}c${i}`} type="button" className="cite" aria-pressed={selected === id}
          title={titles[id] ?? id} aria-label={`Record ${n}${titles[id] ? `: ${titles[id]}` : ""}`}
          onClick={() => onCite(id)}>{n}</button>);
      last = (m.index ?? 0) + m[0].length;
      i++;
    }
    out.push(...bold(s.slice(last), `${key}e`));
    return out;
  };

  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (!list.length) return;
    const items = list;
    blocks.push(<ul key={`l${blocks.length}`}>{items.map((li, j) => <li key={j}>{inline(li, `l${blocks.length}-${j}`)}</li>)}</ul>);
    list = [];
  };
  for (const raw of body.split("\n")) {
    const line = raw.trim();
    const item = line.match(/^[-*•]\s+(.*)$/);
    if (item) { list.push(item[1]); continue; }
    flush();
    if (!line) continue;
    const note = line.match(/^Note:\s*(.*)$/);
    blocks.push(note
      ? <p key={`p${blocks.length}`} className="note">{inline(note[1], `n${blocks.length}`)}</p>
      : <p key={`p${blocks.length}`}>{inline(line, `p${blocks.length}`)}</p>);
  }
  flush();
  return (
    <div className="answer-text">
      {blocks}
      {citedWeb.length > 0 && (
        <p className="web-sources">From the web, not the City:{" "}
          {citedWeb.map((w, j) => (
            <Fragment key={w.id}>{j > 0 && "; "}<a href={w.url} target="_blank" rel="noopener noreferrer">{w.title || w.site}</a>
              {" "}· {w.site}</Fragment>
          ))}
        </p>
      )}
    </div>
  );
}

function Globe() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4">
      <circle cx="8" cy="8" r="6.3" /><path d="M1.8 8h12.4M8 1.7c1.9 1.8 2.8 3.9 2.8 6.3S9.9 12.5 8 14.3M8 1.7C6.1 3.5 5.2 5.6 5.2 8s.9 4.5 2.8 6.3" />
    </svg>
  );
}

function External() {
  return (
    <svg width="10" height="10" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" className="web-cite-ext">
      <path d="M9 2.5h4.5V7M13.5 2.5 7 9M11.5 9.5v3.5a.5.5 0 0 1-.5.5H3a.5.5 0 0 1-.5-.5V5a.5.5 0 0 1 .5-.5h3.5" />
    </svg>
  );
}

function bold(s: string, key: string): ReactNode[] {
  return s.split(/\*\*(.+?)\*\*/g).map((part, i) =>
    i % 2 ? <strong key={`${key}b${i}`}>{part}</strong> : <Fragment key={`${key}s${i}`}>{part}</Fragment>);
}

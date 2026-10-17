import { Fragment, type ReactNode } from "react";

// The citation forms the API accepts (see backend/penn/agent/citations.py).
const CITE = /[\[【]\s*(?:\{\s*"id"\s*:\s*")?(?:id:\s*)?([a-z0-9_]+:[A-Za-z0-9_:\-.]+)"?\s*\}?\s*[\]】]/g;

/** Citation ids in order of first appearance, so chip numbers match across the answer. */
export function citationOrder(text: string): string[] {
  const order: string[] = [];
  for (const m of clean(text).matchAll(CITE)) if (!order.includes(m[1])) order.push(m[1]);
  return order;
}

function clean(text: string) {
  return text
    .replace(/\b(?:lane_closures|event_closures|philly_311|visitphilly):[A-Za-z0-9_:\-.]+\s*(?=[\[【])/g, "")  // an ID written out right before its own citation
    .replace(/\[id:\s*none[^\]]*\]/gi, "")                                                        // "[id:none ...]" is not a citation
    .trim();
}

type Props = { text: string; titles: Record<string, string>; selected: string | null; onCite: (id: string) => void };

/** The model's light markdown (paragraphs, "- " lists, **bold**) with each citation as a numbered chip. */
export function AnswerText({ text, titles, selected, onCite }: Props) {
  const body = clean(text);
  const order = citationOrder(text);

  const inline = (s: string, key: string): ReactNode[] => {
    const out: ReactNode[] = [];
    let last = 0, i = 0;
    for (const m of s.matchAll(CITE)) {
      out.push(...bold(s.slice(last, m.index), `${key}t${i}`));
      const id = m[1];
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
  return <div className="answer-text">{blocks}</div>;
}

function bold(s: string, key: string): ReactNode[] {
  return s.split(/\*\*(.+?)\*\*/g).map((part, i) =>
    i % 2 ? <strong key={`${key}b${i}`}>{part}</strong> : <Fragment key={`${key}s${i}`}>{part}</Fragment>);
}

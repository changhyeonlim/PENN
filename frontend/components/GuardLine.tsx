import type { Guard } from "@/lib/api";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The model's name as its maker gives it: "NVIDIA Nemotron 3 Super" for "nvidia/nemotron-3-super-120b-a12b";
 *  any other model by its id after the slash. */
export function modelName(id: string): string {
  const m = id.toLowerCase();
  if (!m.includes("nemotron")) return id.split("/").pop() ?? id;
  const size = ["ultra", "super", "nano"].find(s => m.includes(s));
  return `NVIDIA Nemotron${/nemotron-3\b/.test(m) ? " 3" : ""}${size ? ` ${size[0].toUpperCase()}${size.slice(1)}` : ""}`;
}

/** The lines closing an answer: what the checks in code vouch for, what they caught, the note on the answer's
 *  dates ("Upcoming permits may not be filed yet."), and the model that wrote it and where it runs. */
export function GuardLine({ guard, note = "", model }: { guard: Guard | null; note?: string; model?: string | null }) {
  const vouched: string[] = [];
  if (guard?.cited === 1) vouched.push("1 citation, matched to a record PENN retrieved");
  if (guard && guard.cited > 1) vouched.push(`${guard.cited} citations, each matched to a record PENN retrieved`);
  if (guard?.resolved_in_code) vouched.push("place and dates worked out in code");
  const sent = guard?.sent_back ?? [];
  const caught = Array.from(new Set(sent.map(s => s.label)));
  const said = vouched.join("; ");
  if (!said && !caught.length && !note && !model) return null;
  return (
    <div className="seal" role="note" aria-label="Hallucination guard">
      {(said || caught.length > 0) && (
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M8 1.2 2.5 3.3v4.2c0 3.3 2.3 6.1 5.5 7.3 3.2-1.2 5.5-4 5.5-7.3V3.3L8 1.2Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
          <path d="m5.4 8.1 1.8 1.8 3.5-3.6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      <p>
        {(said || caught.length > 0) && <b>Hallucination guard</b>}
        {said && <>: {said.charAt(0).toUpperCase() + said.slice(1)}.</>}
        {caught.length > 0 && (
          <span className="seal-caught">{said ? " " : ": "}{plural(sent.length, "draft")} sent back before you saw it: {caught.join("; ")}.</span>
        )}
        {note && <>{said || caught.length ? " " : ""}{note}</>}
        {/* Credit where the work is seen, as a translation names its translator. */}
        {model && <span className="seal-credit">Written by {modelName(model)} on Nebius Token Factory.</span>}
      </p>
    </div>
  );
}

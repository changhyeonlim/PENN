import type { Guard } from "@/lib/api";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** One line under an answer saying what the checks in code vouch for, and what they caught. */
export function GuardLine({ guard }: { guard: Guard }) {
  const { cited, resolved_in_code, sent_back } = guard;
  if (!cited && !sent_back.length) return null;
  const caught = Array.from(new Set(sent_back.map(s => s.label)));
  return (
    <div className="guard" role="note" aria-label="Hallucination guard">
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 1.2 2.5 3.3v4.2c0 3.3 2.3 6.1 5.5 7.3 3.2-1.2 5.5-4 5.5-7.3V3.3L8 1.2Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
        <path d="m5.4 8.1 1.8 1.8 3.5-3.6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div>
        <p>
          <b>Hallucination guard</b>
          {cited > 0 && <> · {plural(cited, "citation")}, each matched to a record PENN retrieved for this answer</>}
          {resolved_in_code && <> · place and dates worked out in code</>}
        </p>
        {caught.length > 0 && (
          <p className="guard-caught">
            {plural(sent_back.length, "draft")} sent back before you saw it: {caught.join("; ")}.
          </p>
        )}
      </div>
    </div>
  );
}

import { useEffect, useRef, useState, type FormEvent } from "react";
import { saveRole, verifyJudge, type Role } from "@/lib/api";
import s from "./RoleGate.module.css";

const TRY = ["Can I drive to Old City this weekend?", "When is the Philadelphia Marathon, and what closes?",
  "Spruce and 15th next week"];

const Arrow = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 6h7.5M6.5 2.8 9.7 6l-3.2 3.2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

/** The first screen of the public demo: "Which role are you?" over the bare map, whose records, freshness and
 *  neighborhoods load only once a role is chosen (page.tsx). A judge enters the access code from the submission
 *  and gets a reserved allowance of questions; a visitor goes straight in, with a few questions to try (answered
 *  instantly from the cache). This only decides what the page shows: the server checks the code on every question
 *  (backend/penn/guardrails.py), so skipping this screen, or removing it, gets a visitor's allowance, never a
 *  judge's. */
export function RoleGate({ onEnter, notice = "", leaving = false }: {
  onEnter: (role: Role, firstQuestion?: string) => void; notice?: string; leaving?: boolean;
}) {
  // Sent back here because a saved judge code stopped working: open straight on the code entry, with the reason.
  const [judging, setJudging] = useState(!!notice);
  const [code, setCode] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState(notice);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (judging) input.current?.focus(); }, [judging]);

  async function check(e: FormEvent) {
    e.preventDefault();
    const c = code.trim();
    if (!c || checking) return;
    setChecking(true);
    setError("");
    try {
      await verifyJudge(c);
      saveRole("judge", c);
      onEnter("judge");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setChecking(false);
    }
  }

  function visit(q?: string) {
    saveRole("visitor");
    onEnter("visitor", q);
  }

  return (
    <div className={`${s.gate}${leaving ? ` ${s.leaving}` : ""}`} role="dialog" aria-modal="true"
      aria-labelledby="gate-title" aria-describedby="gate-lede">
      <div className={s.panel}>
        <p className={s.kicker}><span className={s.mark}>PENN</span> Philadelphia Events and Neighborhood Navigator</p>
        <h2 id="gate-title" className={s.title}>Which role are you?</h2>
        <p id="gate-lede" className={s.lede}>Street closures, permits and events across the city, on one map you can
          ask about. Choose a role to open it.</p>
        <div className={`${s.cards} ${judging ? s.judging : ""}`}>
          <section className={`${s.card} ${judging ? s.open : ""}`}>
            <button type="button" className={s.pick} onClick={() => setJudging(true)} aria-expanded={judging} disabled={judging}>
              <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><path d="M14 3.5 5.5 7v6.2c0 5 3.6 9.4 8.5 11.3 4.9-1.9 8.5-6.3 8.5-11.3V7L14 3.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><path d="m10 14 2.8 2.8 5.4-5.6" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <span className={s.name}>I am a Judge</span>
              <span className={s.sub}>Enter the access code from the submission for your own allowance of questions.</span>
              {!judging && <span className={s.cue}>Enter access code <Arrow /></span>}
            </button>
            {judging && (
              <form className={s.form} onSubmit={check}>
                <label htmlFor="judge-code" className={s.label}>Judge access code</label>
                <div className={s.row}>
                  <input id="judge-code" ref={input} className={s.input} value={code} onChange={e => setCode(e.target.value)}
                    autoComplete="off" spellCheck={false} aria-invalid={!!error} aria-describedby={error ? "gate-error" : undefined} />
                  <button type="submit" className={s.enter} disabled={!code.trim() || checking}>{checking ? "Checking…" : "Enter"}</button>
                </div>
                {error && <p id="gate-error" className={s.error} role="alert">{error}</p>}
                <button type="button" className={s.back} onClick={() => { setJudging(false); setError(""); }}>Back</button>
              </form>
            )}
          </section>
          <section className={`${s.card} ${s.visitor} ${judging ? s.collapsed : ""}`} aria-hidden={judging}>
            <button type="button" className={s.pick} onClick={() => visit()} tabIndex={judging ? -1 : 0}>
              <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><path d="M14 25s8-7.3 8-13.5a8 8 0 1 0-16 0C6 17.7 14 25 14 25Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><circle cx="14" cy="11.5" r="3" fill="none" stroke="currentColor" strokeWidth="1.8" /></svg>
              <span className={s.name}>I am a Visitor</span>
              <span className={s.sub}>Explore the map and ask about Philadelphia's streets.</span>
              <span className={s.cue}>Open the map <Arrow /></span>
            </button>
            <div className={s.try}>
              <span className={s.tryLabel}>Or start with a question</span>
              {TRY.map(q => (
                <button key={q} type="button" className={s.chip} onClick={() => visit(q)} tabIndex={judging ? -1 : 0}>{q}</button>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

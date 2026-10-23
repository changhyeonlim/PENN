import { useEffect, useLayoutEffect, useState } from "react";

/** One timing for every change of size or presence: quick, and easing out like a sheet settling. */
export const MOTION = { ms: 240, outMs: 160, ease: "cubic-bezier(.2, .8, .2, 1)" };

const still = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Animate an element's own size changes (a key opening, an answer growing, a card folding away). Whatever changes
 *  its content, the element's new size is drawn from the old one over MOTION.ms instead of in one frame. The
 *  ResizeObserver reports after layout and before paint, so the jump is never seen. While one change animates,
 *  the frames it causes are its own and are not chased; the size it settles at becomes the next start. Takes
 *  the element itself (from a callback ref), so one that mounts later is still watched. */
export function useSmoothSize(el: HTMLElement | null, axes: { width?: boolean; height?: boolean } = { height: true }) {
  useLayoutEffect(() => {
    if (!el || typeof ResizeObserver === "undefined") return;
    let last = el.getBoundingClientRect();
    let running: Animation | null = null;
    const ro = new ResizeObserver(() => {
      if (running) return;
      const now = el.getBoundingClientRect();
      const dw = axes.width && Math.abs(now.width - last.width) >= 1;
      const dh = axes.height && Math.abs(now.height - last.height) >= 1;
      const from = last;
      last = now;
      if (!(dw || dh) || still()) return;
      const frame = (r: DOMRect): Keyframe => ({
        ...(dw && { width: `${r.width}px` }), ...(dh && { height: `${r.height}px` }), overflow: "hidden" });
      running = el.animate([frame(from), frame(now)], { duration: MOTION.ms, easing: MOTION.ease });
      running.onfinish = running.oncancel = () => { running = null; last = el.getBoundingClientRect(); };
    });
    ro.observe(el);
    return () => { ro.disconnect(); running?.cancel(); };
  }, [el, axes.width, axes.height]);
}

/** Keep something mounted for its exit: "in" while shown, "out" for MOTION.outMs after it is hidden (for a
 *  leaving class to fade it), then null to unmount. Without motion it goes at once. */
export function usePresence(show: boolean): "in" | "out" | null {
  const [state, setState] = useState<"in" | "out" | null>(show ? "in" : null);
  useEffect(() => {
    if (show) { setState("in"); return; }
    if (still()) { setState(null); return; }
    setState(s => (s ? "out" : null));
    const t = setTimeout(() => setState(null), MOTION.outMs);
    return () => clearTimeout(t);
  }, [show]);
  return state;
}

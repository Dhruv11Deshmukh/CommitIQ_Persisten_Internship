// Small GSAP helpers. Every effect is skipped when the visitor prefers reduced motion.
import { useEffect, useLayoutEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);
export { gsap };

export const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Fade/slide the children matching `selector` into view (on scroll when `scroll` is true). */
export function useReveal(ref, selector, { y = 28, stagger = 0.09, duration = 0.9, scroll = true, delay = 0 } = {}) {
  useLayoutEffect(() => {
    if (!ref.current || reducedMotion()) return;
    const ctx = gsap.context(() => {
      const targets = gsap.utils.toArray(selector);
      if (!targets.length) return;
      gsap.from(targets, {
        y, opacity: 0, duration, stagger, delay, ease: "power3.out", clearProps: "transform,opacity",
        scrollTrigger: scroll ? { trigger: ref.current, start: "top 82%", once: true } : undefined,
      });
    }, ref);
    return () => ctx.revert();
  }, [ref, selector, y, stagger, duration, scroll, delay]);
}

/** 3D tilt that follows the cursor, plus a CSS glare position (--mx / --my). */
export function useTilt(ref, { max = 9, lift = 6 } = {}) {
  useEffect(() => {
    const el = ref.current;
    if (!el || reducedMotion() || window.matchMedia("(hover: none)").matches) return;
    const rx = gsap.quickTo(el, "rotationX", { duration: 0.45, ease: "power3.out" });
    const ry = gsap.quickTo(el, "rotationY", { duration: 0.45, ease: "power3.out" });
    const tz = gsap.quickTo(el, "z", { duration: 0.45, ease: "power3.out" });
    gsap.set(el, { transformPerspective: 900, transformOrigin: "center" });
    const move = (e) => {
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      ry((px - 0.5) * max * 2); rx((0.5 - py) * max * 2); tz(lift);
      el.style.setProperty("--mx", `${px * 100}%`); el.style.setProperty("--my", `${py * 100}%`);
    };
    const leave = () => { rx(0); ry(0); tz(0); };
    el.addEventListener("pointermove", move); el.addEventListener("pointerleave", leave);
    return () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerleave", leave); };
  }, [ref, max, lift]);
}

/** Button that is gently pulled toward the cursor. */
export function useMagnetic(ref, strength = 0.28) {
  useEffect(() => {
    const el = ref.current;
    if (!el || reducedMotion() || window.matchMedia("(hover: none)").matches) return;
    const x = gsap.quickTo(el, "x", { duration: 0.5, ease: "power3.out" });
    const y = gsap.quickTo(el, "y", { duration: 0.5, ease: "power3.out" });
    const move = (e) => {
      const r = el.getBoundingClientRect();
      x((e.clientX - (r.left + r.width / 2)) * strength); y((e.clientY - (r.top + r.height / 2)) * strength);
    };
    const leave = () => { x(0); y(0); };
    el.addEventListener("pointermove", move); el.addEventListener("pointerleave", leave);
    return () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerleave", leave); };
  }, [ref, strength]);
}

/** Count a number up from 0 when it first appears. */
export function animateNumber(el, to, { duration = 1.1 } = {}) {
  if (!el) return;
  if (reducedMotion() || !Number.isFinite(to)) { el.textContent = to; return; }
  const o = { v: 0 };
  gsap.to(o, { v: to, duration, ease: "power2.out", onUpdate: () => (el.textContent = Math.round(o.v)) });
}

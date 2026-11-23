import { useEffect, useRef } from "react";
import * as THREE from "three";
import { gsap, reducedMotion } from "../lib/motion";

/**
 * A slow helix of floating "commitment cards" drawn as fine lines, with drifting dust.
 * variant="hero"    -> large, offset to the right, reacts to scroll (landing page)
 * variant="ambient" -> centred, quieter backdrop (sign in / sign up)
 * Renders nothing if WebGL is unavailable; the page still looks right without it.
 */
function cardOutline(w, h, r) {
  const x = -w / 2, y = -h / 2, s = new THREE.Shape();
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.absarc(x + w - r, y + r, r, -Math.PI / 2, 0);
  s.lineTo(x + w, y + h - r); s.absarc(x + w - r, y + h - r, r, 0, Math.PI / 2);
  s.lineTo(x + r, y + h); s.absarc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
  s.lineTo(x, y + r); s.absarc(x + r, y + r, r, Math.PI, Math.PI * 1.5);
  return s;
}

export default function Hero3D({ variant = "hero", className = "" }) {
  const host = useRef(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch { return; }                                              // no WebGL: fall back to the CSS background

    const hero = variant === "hero";
    const still = reducedMotion();
    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(0x050505, 8, 17);
    const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 60);
    camera.position.set(0, 0, 11);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
    el.appendChild(renderer.domElement);

    // ---- cards -----------------------------------------------------------------
    const W = 2.3, H = 1.38, shape = cardOutline(W, H, 0.14);
    const outline = new THREE.BufferGeometry().setFromPoints(shape.getPoints(10));
    const fill = new THREE.ShapeGeometry(shape);
    const rows = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-0.92, 0.26, 0), new THREE.Vector3(0.45, 0.26, 0),
      new THREE.Vector3(-0.92, -0.04, 0), new THREE.Vector3(0.78, -0.04, 0),
      new THREE.Vector3(-0.92, -0.34, 0), new THREE.Vector3(0.1, -0.34, 0),
    ]);
    const dot = new THREE.CircleGeometry(0.07, 18);
    const mats = [];
    const mat = (m) => { mats.push(m); return m; };
    const white = mat(new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: hero ? 0.42 : 0.26 }));
    const dim = mat(new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: hero ? 0.2 : 0.12 }));
    const glass = mat(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: hero ? 0.035 : 0.02, side: THREE.DoubleSide, depthWrite: false }));
    const red = mat(new THREE.LineBasicMaterial({ color: 0xe50914, transparent: true }));
    const redFill = mat(new THREE.MeshBasicMaterial({ color: 0xe50914, transparent: true, opacity: 0.9 }));
    const okFill = mat(new THREE.MeshBasicMaterial({ color: 0xf4f4f0, transparent: true, opacity: 0.7 }));

    const deck = new THREE.Group();
    const N = hero ? 30 : 18;
    for (let i = 0; i < N; i++) {
      const urgent = i % 7 === 3;                                    // a few "at risk" cards glow red
      const c = new THREE.Group();
      c.add(new THREE.Mesh(fill, glass));
      c.add(new THREE.LineLoop(outline, urgent ? red : white));
      c.add(new THREE.LineSegments(rows, dim));
      const d = new THREE.Mesh(dot, urgent ? redFill : okFill); d.position.set(W / 2 - 0.3, H / 2 - 0.3, 0.001); c.add(d);
      const a = i * 0.62, R = hero ? 4.1 : 4.6;
      c.position.set(Math.cos(a) * R, (i - N / 2) * (hero ? 0.36 : 0.4), Math.sin(a) * R);
      c.rotation.y = Math.PI / 2 - a;
      deck.add(c);
    }
    deck.rotation.set(-0.16, 0, 0.08);
    scene.add(deck);

    // ---- dust ------------------------------------------------------------------
    const P = hero ? 520 : 260, pos = new Float32Array(P * 3);
    for (let i = 0; i < P; i++) {
      const r = 5 + Math.random() * 10, t = Math.random() * Math.PI * 2, u = Math.acos(2 * Math.random() - 1);
      pos.set([r * Math.sin(u) * Math.cos(t), r * Math.cos(u) * 0.8, r * Math.sin(u) * Math.sin(t)], i * 3);
    }
    const dustGeo = new THREE.BufferGeometry(); dustGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const dustMat = mat(new THREE.PointsMaterial({ color: 0xffffff, size: 0.028, transparent: true, opacity: 0.5, sizeAttenuation: true }));
    const dust = new THREE.Points(dustGeo, dustMat);
    scene.add(dust);

    // ---- sizing / layout ---------------------------------------------------------
    const layout = () => {
      const w = el.clientWidth || 1, h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h; camera.updateProjectionMatrix();
      const wide = w / h > 1.25;
      deck.position.x = hero ? (wide ? 3.9 : 0) : 0;
      deck.position.y = hero && !wide ? -2.2 : 0;                    // phones: push the cards below the text
      deck.scale.setScalar(hero ? (wide ? 0.94 : 0.72) : 1);
      const k = hero && !wide ? 0.5 : 1;                             // ...and make them quieter
      white.opacity = (hero ? 0.42 : 0.26) * k; dim.opacity = (hero ? 0.2 : 0.12) * k; glass.opacity = (hero ? 0.035 : 0.02) * k;
      if (still) renderer.render(scene, camera);
    };
    const ro = new ResizeObserver(layout); ro.observe(el); layout();

    // ---- motion ------------------------------------------------------------------
    const mouse = { x: 0, y: 0 }, eased = { x: 0, y: 0 };
    const onMove = (e) => { mouse.x = e.clientX / window.innerWidth - 0.5; mouse.y = e.clientY / window.innerHeight - 0.5; };
    window.addEventListener("pointermove", onMove, { passive: true });

    let visible = true;
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }, { threshold: 0 }); io.observe(el);

    const clock = new THREE.Clock();
    if (!still) {
      gsap.fromTo(renderer.domElement, { opacity: 0 }, { opacity: 1, duration: 1.8, ease: "power2.out" });
      gsap.from(deck.scale, { x: 0.55, y: 0.55, z: 0.55, duration: 2.2, ease: "power3.out" });
      gsap.from(deck.rotation, { y: -1.2, duration: 2.6, ease: "power3.out" });
      renderer.setAnimationLoop(() => {
        if (!visible || document.hidden) return;
        const dt = Math.min(clock.getDelta(), 0.05);
        eased.x += (mouse.x - eased.x) * 0.04; eased.y += (mouse.y - eased.y) * 0.04;
        deck.children.forEach((c) => c.position.y += Math.sin(clock.elapsedTime * 0.6 + c.position.x) * 0.0007);
        deck.rotation.y += dt * 0.11;
        camera.position.x = eased.x * 1.6;
        camera.position.y = -eased.y * 1.0 - (hero ? window.scrollY * 0.0022 : 0);
        camera.lookAt(0, hero ? -window.scrollY * 0.0012 : 0, 0);
        dust.rotation.y -= dt * 0.015;
        renderer.render(scene, camera);
      });
    }

    return () => {
      renderer.setAnimationLoop(null);
      gsap.killTweensOf([renderer.domElement, deck.scale, deck.rotation]);
      window.removeEventListener("pointermove", onMove);
      ro.disconnect(); io.disconnect();
      [outline, fill, rows, dot, dustGeo].forEach((g) => g.dispose());
      mats.forEach((m) => m.dispose());
      renderer.dispose(); renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, [variant]);

  return <div ref={host} className={`absolute inset-0 ${className}`} aria-hidden="true" style={{ pointerEvents: "none" }} />;
}

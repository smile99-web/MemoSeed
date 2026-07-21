"use client";

import { useEffect, useRef } from "react";

/**
 * 全局科技风背景：
 *  - CSS 极光光斑（青/紫/翠）缓慢漂移
 *  - 极细网格线，营造“驾驶舱”科技感
 *  - Canvas 漂浮微粒（上升的光点），轻量 60fps
 */
export function TechBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let rafId = 0;
    let width = 0;
    let height = 0;

    interface Particle {
      x: number;
      y: number;
      r: number;
      speed: number;
      drift: number;
      phase: number;
      hue: number;
      alpha: number;
    }

    const HUES = [187, 160, 262]; // cyan / emerald / violet
    const particles: Particle[] = [];

    function resize() {
      width = window.innerWidth;
      height = window.innerHeight;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas!.width = width * dpr;
      canvas!.height = height * dpr;
      canvas!.style.width = `${width}px`;
      canvas!.style.height = `${height}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function spawn(count: number) {
      particles.length = 0;
      for (let i = 0; i < count; i += 1) {
        particles.push({
          x: Math.random() * width,
          y: Math.random() * height,
          r: 1 + Math.random() * 2.2,
          speed: 0.12 + Math.random() * 0.3,
          drift: 0.15 + Math.random() * 0.35,
          phase: Math.random() * Math.PI * 2,
          hue: HUES[Math.floor(Math.random() * HUES.length)],
          alpha: 0.12 + Math.random() * 0.22,
        });
      }
    }

    function tick(time: number) {
      ctx!.clearRect(0, 0, width, height);
      for (const p of particles) {
        p.y -= p.speed;
        p.x += Math.sin(time / 2400 + p.phase) * p.drift * 0.12;
        if (p.y < -8) {
          p.y = height + 8;
          p.x = Math.random() * width;
        }
        const twinkle = 0.65 + 0.35 * Math.sin(time / 900 + p.phase * 3);
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx!.fillStyle = `hsla(${p.hue} 90% 46% / ${(p.alpha * twinkle).toFixed(3)})`;
        ctx!.shadowColor = `hsla(${p.hue} 90% 50% / 0.6)`;
        ctx!.shadowBlur = 6;
        ctx!.fill();
        ctx!.shadowBlur = 0;
      }
      rafId = requestAnimationFrame(tick);
    }

    resize();
    spawn(Math.min(70, Math.floor((width * height) / 26000)));
    rafId = requestAnimationFrame(tick);

    const onResize = () => {
      resize();
      spawn(Math.min(70, Math.floor((width * height) / 26000)));
    };
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      {/* 极光光斑 */}
      <div className="animate-aurora absolute -top-32 -left-24 h-[34rem] w-[34rem] rounded-full bg-cyan-300/25 blur-3xl" />
      <div className="animate-aurora absolute top-[12%] -right-32 h-[30rem] w-[30rem] rounded-full bg-violet-300/20 blur-3xl [animation-delay:-6s]" />
      <div className="animate-aurora absolute -bottom-40 left-[28%] h-[32rem] w-[32rem] rounded-full bg-emerald-300/20 blur-3xl [animation-delay:-11s]" />
      {/* 细网格 */}
      <div
        className="absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "linear-gradient(to right, rgb(100 116 139 / 0.055) 1px, transparent 1px), linear-gradient(to bottom, rgb(100 116 139 / 0.055) 1px, transparent 1px)",
          backgroundSize: "44px 44px",
          maskImage: "radial-gradient(ellipse 90% 80% at 50% 30%, black 30%, transparent 78%)",
          WebkitMaskImage: "radial-gradient(ellipse 90% 80% at 50% 30%, black 30%, transparent 78%)",
        }}
      />
      {/* 漂浮微粒 */}
      <canvas ref={canvasRef} className="absolute inset-0" />
    </div>
  );
}

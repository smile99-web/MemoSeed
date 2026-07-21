"use client";

import { useEffect, useRef } from "react";

/**
 * 全局科技风背景：
 *  - CSS 极光光斑（青/紫/翠）缓慢漂移
 *  - 极细网格线，营造"驾驶舱"科技感
 *  - Canvas 漂浮微粒（上升的光点）
 * 性能（iPad 关键）：微粒用预渲染 sprite（drawImage，无逐帧 shadowBlur），
 * 触屏设备减量，页面隐藏时暂停 rAF。
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
      sprite: HTMLCanvasElement;
      alpha: number;
    }

    const HUES = [187, 160, 262]; // cyan / emerald / violet
    const particles: Particle[] = [];

    // 预渲染光点 sprite：径向渐变一次，之后 drawImage，避免逐帧 shadowBlur。
    const spriteCache = new Map<number, HTMLCanvasElement>();
    function getSprite(hue: number): HTMLCanvasElement {
      const cached = spriteCache.get(hue);
      if (cached) return cached;
      const size = 32;
      const sprite = document.createElement("canvas");
      sprite.width = size;
      sprite.height = size;
      const sctx = sprite.getContext("2d")!;
      const gradient = sctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
      gradient.addColorStop(0, `hsla(${hue} 90% 50% / 0.9)`);
      gradient.addColorStop(0.35, `hsla(${hue} 90% 55% / 0.45)`);
      gradient.addColorStop(1, `hsla(${hue} 90% 55% / 0)`);
      sctx.fillStyle = gradient;
      sctx.fillRect(0, 0, size, size);
      spriteCache.set(hue, sprite);
      return sprite;
    }

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

    function particleBudget(): number {
      const coarse = window.matchMedia("(pointer: coarse)").matches;
      const cap = coarse ? 28 : 60;
      return Math.min(cap, Math.floor((width * height) / 32000));
    }

    function spawn() {
      particles.length = 0;
      const count = particleBudget();
      for (let i = 0; i < count; i += 1) {
        particles.push({
          x: Math.random() * width,
          y: Math.random() * height,
          r: 6 + Math.random() * 14,
          speed: 0.1 + Math.random() * 0.25,
          drift: 0.15 + Math.random() * 0.35,
          phase: Math.random() * Math.PI * 2,
          sprite: getSprite(HUES[Math.floor(Math.random() * HUES.length)]),
          alpha: 0.25 + Math.random() * 0.4,
        });
      }
    }

    function tick(time: number) {
      ctx!.clearRect(0, 0, width, height);
      for (const p of particles) {
        p.y -= p.speed;
        p.x += Math.sin(time / 2400 + p.phase) * p.drift * 0.12;
        if (p.y < -20) {
          p.y = height + 20;
          p.x = Math.random() * width;
        }
        const twinkle = 0.6 + 0.4 * Math.sin(time / 900 + p.phase * 3);
        ctx!.globalAlpha = p.alpha * twinkle;
        ctx!.drawImage(p.sprite, p.x - p.r / 2, p.y - p.r / 2, p.r, p.r);
      }
      ctx!.globalAlpha = 1;
      rafId = requestAnimationFrame(tick);
    }

    function start() {
      if (!rafId) rafId = requestAnimationFrame(tick);
    }
    function stop() {
      if (rafId) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
    }
    function onVisibility() {
      if (document.hidden) stop();
      else start();
    }

    resize();
    spawn();
    start();

    const onResize = () => {
      resize();
      spawn();
    };
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
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

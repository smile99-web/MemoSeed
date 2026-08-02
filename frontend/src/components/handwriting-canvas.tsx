"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";

/**
 * On-screen handwriting canvas (ported from the tingxie 语文听写 app).
 *
 * The child writes with Apple Pencil / finger / mouse via Pointer Events
 * (pressure-sensitive strokes). Three guide styles:
 * - "english": 四线三格 — the four-line English copybook grid;
 * - "chinese": 米字格 cells, one per expected character (tingxie's grid);
 * - "both":    每日一测 — English four-line grid on top + one row of
 *              Chinese 米字格 below; the child writes the English word AND
 *              its Chinese meaning on one canvas, judged as one image.
 *
 * exportImage() renders a clean white-background PNG data URL — that image,
 * grid included, is what the vision LLM sees.
 */

export type HandwritingGridStyle = "english" | "chinese" | "both";

export interface HandwritingCanvasHandle {
  undo: () => void;
  clear: () => void;
  isEmpty: () => boolean;
  exportImage: () => string;
}

interface StrokePoint {
  x: number;
  y: number;
  p: number;
}

interface HandwritingCanvasProps {
  gridStyle: HandwritingGridStyle;
  /** 米字格 cell count (chinese style only). */
  cells?: number;
  /** Called on every completed stroke segment so parents can treat drawing
   * as study activity (keeps the study timer alive while writing). */
  onDraw?: () => void;
}

const ENGLISH_HEIGHT = 180;
const BOTH_ENGLISH_HEIGHT = 132;
const BOTH_GAP = 16;
const CHINESE_CELL_MIN = 72;
const CHINESE_CELL_MAX = 120;
const BOTH_CELL_MIN = 56;
const BOTH_CELL_MAX = 92;
const MAX_WIDTH = 760;

function drawEnglishGrid(ctx: CanvasRenderingContext2D, width: number, height: number) {
  const top = height * 0.08;
  const upper = height * 0.36;
  const base = height * 0.68;
  const bottom = height * 0.92;
  ctx.save();
  // Baseline solid — the line letters sit on.
  ctx.strokeStyle = "#94a3b8";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(4, base);
  ctx.lineTo(width - 4, base);
  ctx.stroke();
  // The other three lines dashed.
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([7, 6]);
  for (const y of [top, upper, bottom]) {
    ctx.beginPath();
    ctx.moveTo(4, y);
    ctx.lineTo(width - 4, y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawChineseGrid(ctx: CanvasRenderingContext2D, cell: number, count: number) {
  ctx.save();
  for (let i = 0; i < count; i += 1) {
    const x0 = i * cell;
    ctx.strokeStyle = "#c8a87c";
    ctx.lineWidth = 2;
    ctx.strokeRect(x0 + 1, 1, cell - 2, cell - 2);
    ctx.strokeStyle = "#e8dcc8";
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 5]);
    ctx.beginPath();
    ctx.moveTo(x0 + cell / 2, 0); ctx.lineTo(x0 + cell / 2, cell);
    ctx.moveTo(x0, cell / 2); ctx.lineTo(x0 + cell, cell / 2);
    ctx.moveTo(x0, 0); ctx.lineTo(x0 + cell, cell);
    ctx.moveTo(x0 + cell, 0); ctx.lineTo(x0, cell);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.restore();
}

const HandwritingCanvas = forwardRef<HandwritingCanvasHandle, HandwritingCanvasProps>(
  function HandwritingCanvas({ gridStyle, cells = 4, onDraw }, ref) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const strokesRef = useRef<StrokePoint[][]>([]);
    const currentStrokeRef = useRef<StrokePoint[] | null>(null);
    const sizeRef = useRef({ cssW: 0, cssH: 0, dpr: 1, cell: 0 });
    // Keep the callback in a ref: parents pass inline arrows (new identity
    // every render), which would otherwise re-attach all pointer listeners
    // on every render.
    const onDrawRef = useRef(onDraw);
    onDrawRef.current = onDraw;

    const layout = useCallback(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      let cssW: number;
      let cssH: number;
      let cell = 0;
      if (gridStyle === "chinese") {
        const maxW = Math.min(window.innerWidth - 60, MAX_WIDTH);
        cell = Math.max(CHINESE_CELL_MIN, Math.min(CHINESE_CELL_MAX, Math.floor(maxW / Math.max(1, cells))));
        cssW = cell * Math.max(1, cells);
        cssH = cell;
      } else if (gridStyle === "both") {
        cssW = Math.min(window.innerWidth - 60, MAX_WIDTH);
        cell = Math.max(BOTH_CELL_MIN, Math.min(BOTH_CELL_MAX, Math.floor(cssW / Math.max(1, cells))));
        cssH = BOTH_ENGLISH_HEIGHT + BOTH_GAP + cell;
      } else {
        cssW = Math.min(window.innerWidth - 60, MAX_WIDTH);
        cssH = ENGLISH_HEIGHT;
      }
      sizeRef.current = { cssW, cssH, dpr, cell };
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
    }, [cells, gridStyle]);

    const drawSegment = useCallback((ctx: CanvasRenderingContext2D, a: StrokePoint, b: StrokePoint) => {
      const { dpr } = sizeRef.current;
      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.strokeStyle = "#1a1a1a";
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      const pressure = ((a.p || 0.5) + (b.p || 0.5)) / 2;
      ctx.lineWidth = 3 + pressure * 7;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      ctx.restore();
    }, []);

    const drawGridOn = useCallback((ctx: CanvasRenderingContext2D) => {
      const { cssW, cssH, cell } = sizeRef.current;
      if (gridStyle === "chinese") {
        drawChineseGrid(ctx, cell, Math.max(1, cells));
      } else if (gridStyle === "both") {
        drawEnglishGrid(ctx, cssW, BOTH_ENGLISH_HEIGHT);
        ctx.save();
        ctx.translate(0, BOTH_ENGLISH_HEIGHT + BOTH_GAP);
        drawChineseGrid(ctx, cell, Math.max(1, cells));
        ctx.restore();
      } else {
        drawEnglishGrid(ctx, cssW, cssH);
      }
    }, [cells, gridStyle]);

    const redraw = useCallback(() => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (!canvas || !ctx) return;
      const { dpr } = sizeRef.current;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawGridOn(ctx);
      for (const stroke of strokesRef.current) {
        for (let i = 1; i < stroke.length; i += 1) {
          drawSegment(ctx, stroke[i - 1], stroke[i]);
        }
      }
    }, [drawGridOn, drawSegment]);

    useEffect(() => {
      layout();
      redraw();
    }, [layout, redraw]);

    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      // Block Safari's scroll/zoom gestures while writing.
      const prevent = (e: TouchEvent) => e.preventDefault();
      canvas.addEventListener("touchstart", prevent, { passive: false });
      canvas.addEventListener("touchmove", prevent, { passive: false });

      const pos = (e: PointerEvent): StrokePoint => {
        const rect = canvas.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top, p: e.pressure || 0.5 };
      };
      const onDown = (e: PointerEvent) => {
        if (!e.isPrimary) return;
        e.preventDefault();
        canvas.setPointerCapture(e.pointerId);
        const stroke = [pos(e)];
        currentStrokeRef.current = stroke;
        strokesRef.current.push(stroke);
        onDrawRef.current?.();
      };
      const onMove = (e: PointerEvent) => {
        const stroke = currentStrokeRef.current;
        if (!stroke || !e.isPrimary) return;
        e.preventDefault();
        const point = pos(e);
        const prev = stroke[stroke.length - 1];
        stroke.push(point);
        const ctx = canvas.getContext("2d");
        if (ctx) drawSegment(ctx, prev, point);
        // Coalesce move events: one activity ping per animation frame is
        // plenty for the study timer's activity clock.
        if (stroke.length % 12 === 0) onDrawRef.current?.();
      };
      const onEnd = (e: PointerEvent) => {
        if (e.isPrimary) currentStrokeRef.current = null;
      };
      canvas.addEventListener("pointerdown", onDown);
      canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerup", onEnd);
      canvas.addEventListener("pointercancel", onEnd);
      return () => {
        canvas.removeEventListener("touchstart", prevent);
        canvas.removeEventListener("touchmove", prevent);
        canvas.removeEventListener("pointerdown", onDown);
        canvas.removeEventListener("pointermove", onMove);
        canvas.removeEventListener("pointerup", onEnd);
        canvas.removeEventListener("pointercancel", onEnd);
      };
    }, [drawSegment]);

    useImperativeHandle(ref, () => ({
      undo: () => {
        strokesRef.current.pop();
        redraw();
      },
      clear: () => {
        strokesRef.current = [];
        redraw();
      },
      isEmpty: () => strokesRef.current.length === 0,
      exportImage: () => {
        const { cssW, cssH, cell } = sizeRef.current;
        const scale = Math.min(1, 1100 / Math.max(1, cssW));
        const off = document.createElement("canvas");
        off.width = Math.round(cssW * scale);
        off.height = Math.round(cssH * scale);
        const ctx = off.getContext("2d");
        if (!ctx) return "";
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, off.width, off.height);
        ctx.scale(scale, scale);
        if (gridStyle === "chinese") {
          drawChineseGrid(ctx, cell, Math.max(1, cells));
        } else if (gridStyle === "both") {
          drawEnglishGrid(ctx, cssW, BOTH_ENGLISH_HEIGHT);
          ctx.save();
          ctx.translate(0, BOTH_ENGLISH_HEIGHT + BOTH_GAP);
          drawChineseGrid(ctx, cell, Math.max(1, cells));
          ctx.restore();
        } else {
          drawEnglishGrid(ctx, cssW, cssH);
        }
        for (const stroke of strokesRef.current) {
          for (let i = 1; i < stroke.length; i += 1) {
            const a = stroke[i - 1];
            const b = stroke[i];
            const pressure = ((a.p || 0.5) + (b.p || 0.5)) / 2;
            ctx.strokeStyle = "#1a1a1a";
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.lineWidth = 3 + pressure * 7;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
        return off.toDataURL("image/png");
      },
    }), [cells, gridStyle, redraw]);

    return (
      <canvas
        ref={canvasRef}
        data-testid="handwriting-canvas"
        className="touch-none rounded-xl border border-slate-200 bg-white shadow-inner"
        style={{ display: "block", margin: "0 auto" }}
      />
    );
  },
);

export default HandwritingCanvas;

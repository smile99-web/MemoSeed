"use client";

import { memo, useEffect, useRef } from "react";

interface CelebrationConfettiProps {
  runKey: number;
}

const CelebrationConfetti = memo(function CelebrationConfetti({ runKey }: CelebrationConfettiProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) {
      return;
    }

    const dpr = window.devicePixelRatio || 1;
    const resizeCanvas = () => {
      canvas.width = Math.floor(canvas.clientWidth * dpr);
      canvas.height = Math.floor(canvas.clientHeight * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resizeCanvas();

    const colors = ["#10b981", "#0ea5e9", "#f59e0b", "#ef4444", "#8b5cf6"];
    const pieces = Array.from({ length: 150 }, () => ({
      x: Math.random() * canvas.clientWidth,
      y: -20 - Math.random() * canvas.clientHeight * 0.6,
      size: 6 + Math.random() * 7,
      color: colors[Math.floor(Math.random() * colors.length)],
      rotation: Math.random() * Math.PI,
      rotationSpeed: -0.18 + Math.random() * 0.36,
      speedX: -2.5 + Math.random() * 5,
      speedY: 2 + Math.random() * 4,
    }));

    let animationFrame = 0;
    const startedAt = performance.now();
    const drawFrame = (time: number) => {
      context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
      pieces.forEach((piece) => {
        piece.x += piece.speedX;
        piece.y += piece.speedY;
        piece.rotation += piece.rotationSpeed;
        if (piece.y > canvas.clientHeight + 30) {
          piece.y = -20;
          piece.x = Math.random() * canvas.clientWidth;
        }

        context.save();
        context.translate(piece.x, piece.y);
        context.rotate(piece.rotation);
        context.fillStyle = piece.color;
        context.fillRect(-piece.size / 2, -piece.size / 2, piece.size, piece.size * 0.55);
        context.restore();
      });

      if (time - startedAt < 5200) {
        animationFrame = window.requestAnimationFrame(drawFrame);
      }
    };

    animationFrame = window.requestAnimationFrame(drawFrame);
    window.addEventListener("resize", resizeCanvas);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", resizeCanvas);
    };
  }, [runKey]);

  return <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />;
});

export default CelebrationConfetti;

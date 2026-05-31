"use client";

import { memo, useEffect, useRef, useState } from "react";

interface MiniCelebrationConfettiProps {
  triggerKey: number;
  speakMessage?: string;
}

const COLORS = ["#10b981", "#0ea5e9", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];

const MiniCelebrationConfetti = memo(function MiniCelebrationConfetti({ triggerKey, speakMessage }: MiniCelebrationConfettiProps) {
  const [visible, setVisible] = useState(false);
  const circles = useRef<Array<{ size: number; color: string; tx: number; ty: number; delay: number }>>([]);

  if (circles.current.length === 0) {
    for (let i = 0; i < 50; i += 1) {
      circles.current.push({
        size: 6 + Math.random() * 8,
        color: COLORS[i % COLORS.length],
        tx: -250 + Math.random() * 500,
        ty: -250 + Math.random() * 500,
        delay: Math.random() * 0.3,
      });
    }
  }

  useEffect(() => {
    if (triggerKey <= 0) return;
    setVisible(true);
    if (speakMessage && "speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance(speakMessage);
      u.lang = "zh-CN";
      u.rate = 0.9;
      window.speechSynthesis.speak(u);
    }
    const t = setTimeout(() => setVisible(false), 2500);
    return () => clearTimeout(t);
  }, [speakMessage, triggerKey]);

  if (!visible) return null;

  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center overflow-hidden">
      {circles.current.map((c, i) => (
        <div
          key={i}
          className="absolute rounded-full"
          style={{
            width: `${c.size}px`,
            height: `${c.size}px`,
            backgroundColor: c.color,
            animation: `confetti-burst 2s ease-out ${c.delay}s forwards`,
            left: "50%",
            top: "50%",
            "--tx": c.tx,
            "--ty": c.ty,
          } as React.CSSProperties}
        />
      ))}
    </div>
  );
});

export default MiniCelebrationConfetti;

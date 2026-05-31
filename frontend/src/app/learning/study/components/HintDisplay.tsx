"use client";

import { memo } from "react";

interface HintDisplayProps {
  word: string;
  chunks: string[];
  matchedPrefixLength: number;
  onPlayChunk: (chunkIndex: number) => void;
  onPlayPhonics: (chunkIndex: number) => void;
  graphemePhonemeMap?: Record<string, string> | null;
  cachedSyllables?: string[] | null;
}

const syllableBgColors = ["bg-sky-100 border-sky-500", "bg-emerald-100 border-emerald-500", "bg-amber-100 border-amber-500"];

const HintDisplay = memo(function HintDisplay({
  chunks,
  matchedPrefixLength,
  onPlayChunk,
  onPlayPhonics,
  graphemePhonemeMap,
}: HintDisplayProps) {
  let currentPos = 0;

  return (
    <div className="flex flex-wrap items-center justify-center gap-2 mt-2">
      {chunks.map((chunk, i) => {
        const chunkEnd = currentPos + chunk.length;
        currentPos = chunkEnd;
        const isCorrect = matchedPrefixLength >= chunkEnd;
        const bgClass = isCorrect ? "bg-emerald-200 border-emerald-500" : syllableBgColors[i % syllableBgColors.length];
        const textClass = isCorrect ? "text-emerald-800" : "text-slate-800";

        return (
          <span key={i} className="inline-flex items-center gap-1">
            <span className={`px-3 py-1.5 rounded-lg border-2 font-bold text-lg ${bgClass} ${textClass} transition-colors`}>
              {chunk}
            </span>
            <button
              type="button"
              className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-500 hover:text-slate-700 transition-colors"
              onClick={() => onPlayChunk(i)}
              title={`听 ${chunk} 的发音`}
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
              </svg>
            </button>
            <button
              type="button"
              className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-400 hover:text-slate-600 transition-colors"
              onClick={() => onPlayPhonics(i)}
              title={graphemePhonemeMap && Object.keys(graphemePhonemeMap).length > 0
                ? `自然拼读发音：${chunk}`
                : `逐字母发音：${chunk.split("").join(" ")}`}
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                <line x1="12" y1="19" x2="12" y2="23" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          </span>
        );
      })}
    </div>
  );
});

export default HintDisplay;

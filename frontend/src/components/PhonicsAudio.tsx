"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getAccessToken } from "@/lib/auth";
import { generatePhonicsDeckAudio, getPhonicsDeck, PhonicsDeckItem, playCachedAudio, stopAudioPlayback } from "@/lib/tts";

interface PhonicsAudioProps {
  phonemeKeys: string[];
  autoPlay?: boolean;
  className?: string;
}

export default function PhonicsAudio({ phonemeKeys, autoPlay = false, className = "" }: PhonicsAudioProps) {
  void autoPlay;
  const [phonicsDeck, setPhonicsDeck] = useState<Map<string, PhonicsDeckItem>>(new Map());
  const [isGenerating, setIsGenerating] = useState(false);
  const [generationProgress, setGenerationProgress] = useState("");
  const loadedRef = useRef(false);

  const loadPhonicsDeck = useCallback(async () => {
    if (loadedRef.current) return;
    loadedRef.current = true;

    const accessToken = getAccessToken();
    if (!accessToken) return;

    try {
      const deck = await getPhonicsDeck(accessToken);
      const deckMap = new Map<string, PhonicsDeckItem>();
      deck.phonemes.forEach((item) => deckMap.set(item.phoneme_key, item));
      setPhonicsDeck(deckMap);
    } catch {
      // Deck not available, try generating
    }
  }, []);

  const handleGeneratePhonics = useCallback(async () => {
    const accessToken = getAccessToken();
    if (!accessToken) return;

    setIsGenerating(true);
    setGenerationProgress("Generating phonics audio...");
    try {
      const result = await generatePhonicsDeckAudio(accessToken);
      setGenerationProgress(`Done: ${result.generated} generated, ${result.cached} cached, ${result.errors} errors`);
      loadedRef.current = false;
      await loadPhonicsDeck();
    } catch {
      setGenerationProgress("Failed to generate phonics audio");
    } finally {
      setIsGenerating(false);
    }
  }, [loadPhonicsDeck]);

  useEffect(() => {
    void loadPhonicsDeck();
  }, [loadPhonicsDeck]);

  const handlePlayPhoneme = useCallback(async (item: PhonicsDeckItem) => {
    stopAudioPlayback();
    try {
      await playCachedAudio(item.audio_url);
    } catch {
      // Fallback to browser speech
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(item.synth_text);
        utterance.lang = "en-US";
        utterance.rate = 0.4;
        window.speechSynthesis.speak(utterance);
      }
    }
  }, []);

  if (phonicsDeck.size === 0) {
    return (
      <div className={className}>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-50"
          disabled={isGenerating}
          onClick={() => void handleGeneratePhonics()}
        >
          {isGenerating ? generationProgress : "Generate Phonics Deck"}
        </button>
      </div>
    );
  }

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      {phonemeKeys.map((key) => {
        const item = phonicsDeck.get(key);
        if (!item) return null;
        return (
          <button
            key={key}
            type="button"
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 hover:border-slate-300 transition-colors"
            onClick={() => void handlePlayPhoneme(item)}
            title={item.display_label}
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4 text-slate-500">
              <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
            </svg>
            <span>{item.display_label}</span>
          </button>
        );
      })}
    </div>
  );
}

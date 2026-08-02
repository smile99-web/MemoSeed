// Voice capture for the read-aloud ("echo") step.
//
// Two modes:
// 1. Pronunciation check (primary): record the child with MediaRecorder,
//    auto-stop on end-of-speech (VAD), convert to 16 kHz mono WAV and upload
//    to /learning/pronunciation-check for ASR + lenient scoring.
// 2. Loudness detection (degraded fallback): when the mic or ASR is
//    unavailable we fall back to detecting voice-LEVEL sound only.
//
// Requires a secure context (HTTPS) + mic permission. When unsupported the
// UI falls back to the manual "我读完了" button.

import { fetchWithAuth, parseApiError } from "@/lib/api";
import { getApiBaseUrl } from "@/lib/api-base-url";

let audioContext: AudioContext | null = null;
let mediaStream: MediaStream | null = null;

export function isVoiceEchoSupported(): boolean {
  return (
    typeof window !== "undefined"
    && window.isSecureContext === true
    && typeof navigator !== "undefined"
    && !!navigator.mediaDevices?.getUserMedia
  );
}

/** Request the mic once and keep the stream for the whole study session. */
export async function initVoiceEcho(): Promise<boolean> {
  if (!isVoiceEchoSupported()) {
    return false;
  }
  if (mediaStream) {
    return true;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return true;
  } catch {
    mediaStream = null;
    return false;
  }
}

/**
 * Listen for voice-level sound for up to `windowMs` milliseconds.
 * Resolves true as soon as enough loud sound is detected (cumulative loud
 * time >= minLoudMs, or one sharp peak when allowPeak), false when the
 * window expires. `onVolume` receives 0..1 levels for the animated wave.
 *
 * The echo gate passes a text-scaled minLoudMs and allowPeak=false: a single
 * shouted first word (or a clap) must NOT count as "read the sentence".
 */
export function listenForVoice(
  windowMs: number,
  onVolume?: (level: number) => void,
  options: { minLoudMs?: number; allowPeak?: boolean } = {},
): Promise<boolean> {
  const minLoudMs = options.minLoudMs ?? 350;
  const allowPeak = options.allowPeak ?? true;
  return new Promise((resolve) => {
    if (!mediaStream) {
      resolve(false);
      return;
    }
    try {
      if (!audioContext) {
        const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (!Ctor) {
          resolve(false);
          return;
        }
        audioContext = new Ctor();
      }
      const ctx = audioContext;
      void ctx.resume().catch(() => undefined);
      const source = ctx.createMediaStreamSource(mediaStream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const buffer = new Uint8Array(analyser.fftSize);

      const LOUD_RMS = 0.03;
      const PEAK_RMS = 0.12;
      const startedAt = Date.now();
      let loudMs = 0;
      let lastTick = startedAt;
      let done = false;

      const finish = (detected: boolean) => {
        if (done) {
          return;
        }
        done = true;
        window.clearInterval(timer);
        source.disconnect();
        analyser.disconnect();
        resolve(detected);
      };

      const timer = window.setInterval(() => {
        const now = Date.now();
        const elapsed = now - lastTick;
        lastTick = now;
        analyser.getByteTimeDomainData(buffer);
        let sumSquares = 0;
        for (let i = 0; i < buffer.length; i += 1) {
          const centered = (buffer[i] - 128) / 128;
          sumSquares += centered * centered;
        }
        const rms = Math.sqrt(sumSquares / buffer.length);
        onVolume?.(Math.min(1, rms * 8));
        if (rms >= LOUD_RMS) {
          loudMs += elapsed;
        }
        if ((allowPeak && rms >= PEAK_RMS) || loudMs >= minLoudMs) {
          finish(true);
          return;
        }
        if (now - startedAt >= windowMs) {
          finish(false);
        }
      }, 90);
    } catch {
      resolve(false);
    }
  });
}

/** Release the mic (call when leaving the study page). */
export function shutdownVoiceEcho(): void {
  cancelActiveRecording();
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    void audioContext.close().catch(() => undefined);
    audioContext = null;
  }
}

// ---------------------------------------------------------------------------
// Pronunciation check: record → VAD end-of-speech → WAV → ASR scoring
// ---------------------------------------------------------------------------

export interface PronunciationAttempt {
  status: "ok" | "no-speech" | "cancelled" | "error";
  transcript?: string;
  score?: number;
  passed?: boolean;
  message?: string;
}

interface RecordOptions {
  onVolume?: (level: number) => void;
  /** Fired once when recording ends and the clip goes to the ASR backend. */
  onChecking?: () => void;
  /** Hard cap on the recording window (default 10 s). */
  maxDurationMs?: number;
  /** Stop after this much trailing silence once speech started (default 1.3 s). */
  silenceMs?: number;
  /** No speech at all within this window resolves as no-speech (default 8 s). */
  startTimeoutMs?: number;
}

/** MediaRecorder on top of the mic check — needed for pronunciation ASR. */
export function isPronunciationCheckSupported(): boolean {
  return isVoiceEchoSupported() && typeof MediaRecorder !== "undefined";
}

let activeRecordingCancel: (() => void) | null = null;

/** Abort an in-flight recording (e.g. the echo prompt was dismissed). */
export function cancelActiveRecording(): void {
  const cancel = activeRecordingCancel;
  activeRecordingCancel = null;
  cancel?.();
}

/**
 * Record the child reading `expectedText` aloud and ask the backend to score
 * it. Resolves "no-speech" when nothing loud enough was heard (does NOT count
 * as a failed attempt), "cancelled" when abort via cancelActiveRecording,
 * "error" on mic/encode/network/ASR failures, otherwise "ok" with the score.
 */
export function recordAndRecognize(
  expectedText: string,
  accessToken: string,
  options: RecordOptions = {},
): Promise<PronunciationAttempt> {
  cancelActiveRecording();
  const maxDurationMs = options.maxDurationMs ?? 10000;
  const silenceMs = options.silenceMs ?? 1300;
  const startTimeoutMs = options.startTimeoutMs ?? 8000;

  return new Promise<PronunciationAttempt>((resolve) => {
    if (!mediaStream || !isPronunciationCheckSupported()) {
      resolve({ status: "error", message: "microphone or recorder unavailable" });
      return;
    }

    let settled = false;
    let timer = 0;
    let recorder: MediaRecorder | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let analyser: AnalyserNode | null = null;

    const settle = (result: PronunciationAttempt) => {
      if (settled) {
        return;
      }
      settled = true;
      activeRecordingCancel = null;
      if (timer) {
        window.clearInterval(timer);
      }
      try {
        if (recorder && recorder.state !== "inactive") {
          recorder.stop();
        }
      } catch {
        // already stopped
      }
      try {
        source?.disconnect();
        analyser?.disconnect();
      } catch {
        // already disconnected
      }
      resolve(result);
    };
    activeRecordingCancel = () => settle({ status: "cancelled" });

    const finishWithBlob = async (blob: Blob, speechMs: number): Promise<PronunciationAttempt> => {
      if (blob.size === 0 || speechMs < 250) {
        return { status: "no-speech" };
      }
      try {
        options.onChecking?.();
        const wavBlob = await convertToWav(blob);
        const form = new FormData();
        form.append("expected_text", expectedText);
        form.append("file", wavBlob, "echo.wav");
        const response = await fetchWithAuth(
          `${getApiBaseUrl()}/learning/pronunciation-check`,
          { method: "POST", body: form },
          accessToken,
        );
        if (!response.ok) {
          return { status: "error", message: await parseApiError(response).catch(() => `HTTP ${response.status}`) };
        }
        const body = (await response.json()) as {
          transcript?: string;
          score?: number;
          passed?: boolean;
          heard_speech?: boolean;
        };
        if (body.heard_speech === false) {
          return { status: "no-speech" };
        }
        return {
          status: "ok",
          transcript: body.transcript ?? "",
          score: typeof body.score === "number" ? body.score : 0,
          passed: body.passed === true,
        };
      } catch (error) {
        return { status: "error", message: error instanceof Error ? error.message : String(error) };
      }
    };

    try {
      if (!audioContext) {
        const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (!Ctor) {
          settle({ status: "error", message: "AudioContext unavailable" });
          return;
        }
        audioContext = new Ctor();
      }
      const ctx = audioContext;
      void ctx.resume().catch(() => undefined);

      const chunks: Blob[] = [];
      const mimeType = pickRecorderMimeType();
      recorder = mimeType
        ? new MediaRecorder(mediaStream, { mimeType })
        : new MediaRecorder(mediaStream);
      const activeRecorder = recorder;
      const recorderStopped = new Promise<void>((resolveStopped) => {
        activeRecorder.onstop = () => resolveStopped();
      });
      activeRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunks.push(event.data);
        }
      };
      activeRecorder.start(250);

      source = ctx.createMediaStreamSource(mediaStream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const activeAnalyser = analyser;
      const buffer = new Uint8Array(activeAnalyser.fftSize);

      const LOUD_RMS = 0.03;
      const startedAt = Date.now();
      let speechStartedAt = 0;
      let loudStreakMs = 0;
      let speechMs = 0;
      let trailingSilenceMs = 0;
      let lastTick = startedAt;
      let stopping = false;

      const stopAndRecognize = () => {
        if (stopping) {
          return;
        }
        stopping = true;
        if (timer) {
          window.clearInterval(timer);
          timer = 0;
        }
        try {
          if (activeRecorder.state !== "inactive") {
            activeRecorder.stop();
          }
        } catch {
          // already stopped
        }
        void recorderStopped.then(async () => {
          if (settled) {
            return; // cancelled while the recorder was draining
          }
          const blobType = activeRecorder.mimeType || mimeType || "audio/webm";
          const result = await finishWithBlob(new Blob(chunks, { type: blobType }), speechMs);
          settle(result);
        });
      };

      timer = window.setInterval(() => {
        if (settled) {
          return;
        }
        const now = Date.now();
        const elapsed = now - lastTick;
        lastTick = now;
        activeAnalyser.getByteTimeDomainData(buffer);
        let sumSquares = 0;
        for (let i = 0; i < buffer.length; i += 1) {
          const centered = (buffer[i] - 128) / 128;
          sumSquares += centered * centered;
        }
        const rms = Math.sqrt(sumSquares / buffer.length);
        options.onVolume?.(Math.min(1, rms * 8));
        const loud = rms >= LOUD_RMS;

        if (!speechStartedAt) {
          loudStreakMs = loud ? loudStreakMs + elapsed : 0;
          if (loudStreakMs >= 150) {
            speechStartedAt = now;
            trailingSilenceMs = 0;
          } else if (now - startedAt >= startTimeoutMs) {
            settle({ status: "no-speech" });
            return;
          }
        } else {
          if (loud) {
            speechMs += elapsed;
            trailingSilenceMs = 0;
          } else {
            trailingSilenceMs += elapsed;
          }
          if (trailingSilenceMs >= silenceMs && speechMs >= 250) {
            stopAndRecognize();
            return;
          }
        }
        if (now - startedAt >= maxDurationMs) {
          if (speechStartedAt) {
            stopAndRecognize();
          } else {
            settle({ status: "no-speech" });
          }
        }
      }, 90);
    } catch (error) {
      settle({ status: "error", message: error instanceof Error ? error.message : String(error) });
    }
  });
}

function pickRecorderMimeType(): string {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") {
    return "";
  }
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  for (const candidate of candidates) {
    if (MediaRecorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }
  return "";
}

/** Decode any browser recording container and re-encode as 16 kHz mono WAV. */
async function convertToWav(blob: Blob): Promise<Blob> {
  if (!audioContext) {
    throw new Error("AudioContext unavailable");
  }
  const arrayBuffer = await blob.arrayBuffer();
  const decoded = await new Promise<AudioBuffer>((resolveDecode, rejectDecode) => {
    audioContext!.decodeAudioData(arrayBuffer, resolveDecode, rejectDecode);
  });

  const TARGET_RATE = 16000;
  let samples: Float32Array;
  let sampleRate = decoded.sampleRate;
  const OfflineCtor = window.OfflineAudioContext
    ?? (window as unknown as { webkitOfflineAudioContext?: typeof OfflineAudioContext }).webkitOfflineAudioContext;
  if (OfflineCtor) {
    const frameCount = Math.max(1, Math.ceil(decoded.duration * TARGET_RATE));
    const offline = new OfflineCtor(1, frameCount, TARGET_RATE);
    const bufferSource = offline.createBufferSource();
    bufferSource.buffer = decoded;
    bufferSource.connect(offline.destination);
    bufferSource.start(0);
    const rendered = await offline.startRendering();
    samples = rendered.getChannelData(0);
    sampleRate = TARGET_RATE;
  } else {
    // No OfflineAudioContext: mix channels down at the native rate instead.
    samples = decoded.getChannelData(0);
    if (decoded.numberOfChannels > 1) {
      const mixed = new Float32Array(decoded.length);
      for (let channel = 0; channel < decoded.numberOfChannels; channel += 1) {
        const data = decoded.getChannelData(channel);
        for (let i = 0; i < data.length; i += 1) {
          mixed[i] += data[i] / decoded.numberOfChannels;
        }
      }
      samples = mixed;
    }
  }
  return encodeWavPcm16(samples, sampleRate);
}

function encodeWavPcm16(samples: Float32Array, sampleRate: number): Blob {
  const bytesPerSample = 2;
  const dataBytes = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);
  const writeAscii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, dataBytes, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += bytesPerSample;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

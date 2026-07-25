// Voice-activity detection for the read-aloud ("echo") step.
//
// Deliberately NOT speech recognition: the goal is to get the child to OPEN
// THEIR MOUTH and read English aloud — pronunciation accuracy does not matter
// (parent's explicit requirement). We only detect that a voice-level sound
// happened during the echo window.
//
// Requires a secure context (HTTPS) + mic permission. On the current plain
// HTTP deployment this returns supported=false and the UI falls back to the
// manual "我读完了" button. If the site is later served over HTTPS, real
// detection lights up automatically with no code change.

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
 * time >= minLoudMs, or one sharp peak), false when the window expires.
 * `onVolume` receives 0..1 levels for the animated wave display.
 */
export function listenForVoice(
  windowMs: number,
  onVolume?: (level: number) => void,
): Promise<boolean> {
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
      const MIN_LOUD_MS = 350;
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
        if (rms >= PEAK_RMS || loudMs >= MIN_LOUD_MS) {
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
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    void audioContext.close().catch(() => undefined);
    audioContext = null;
  }
}

import { getFreshAccessToken } from "@/lib/api";
import { getApiBaseUrl } from "@/lib/api-base-url";
import { ModelSettings } from "@/lib/model-settings";

export interface TtsSynthesisOptions {
  speechRate?: number;
  speed?: number;
}

export interface PrefetchCourseAudioResult {
  course_id: string;
  words: Record<string, string>;
  cache_hits: number;
  cache_misses: number;
}

export interface PhonicsDeckItem {
  phoneme_key: string;
  display_label: string;
  synth_text: string;
  audio_url: string;
}

export interface PhonicsDeck {
  phonemes: PhonicsDeckItem[];
}

export async function synthesizeVolcengineSpeech(
  text: string,
  voice: string,
  language: string,
  settings: ModelSettings,
  accessToken: string,
  options: TtsSynthesisOptions = {},
): Promise<Blob> {
  let response = await requestVolcengineSpeech(text, voice, language, settings, accessToken, options);
  if (response.status === 401) {
    const refreshedAccessToken = await getFreshAccessToken();
    if (refreshedAccessToken) {
      response = await requestVolcengineSpeech(text, voice, language, settings, refreshedAccessToken, options);
    }
  }

  if (!response.ok) {
    throw new Error(await parseSpeechError(response));
  }

  return response.blob();
}

export async function synthesizeKokoroSpeech(
  text: string,
  voice: string,
  settings: ModelSettings,
  accessToken: string,
  options: TtsSynthesisOptions = {},
): Promise<Blob> {
  let response = await requestKokoroSpeech(text, voice, settings, accessToken, options);
  if (response.status === 401) {
    const refreshedAccessToken = await getFreshAccessToken();
    if (refreshedAccessToken) {
      response = await requestKokoroSpeech(text, voice, settings, refreshedAccessToken, options);
    }
  }

  if (!response.ok) {
    throw new Error(await parseSpeechError(response));
  }

  return response.blob();
}

async function requestVolcengineSpeech(
  text: string,
  voice: string,
  language: string,
  settings: ModelSettings,
  accessToken: string,
  options: TtsSynthesisOptions,
): Promise<Response> {
  return fetch(`${getApiBaseUrl()}/tts/speech`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      text,
      voice,
      language,
      endpoint: settings.volcengineTtsEndpoint,
      x_api_key: settings.volcengineTtsApiKey,
      resource_id: settings.volcengineTtsResourceId,
      model: settings.volcengineTtsModel,
      speech_rate: options.speechRate,
    }),
  });
}

export async function synthesizeCosyVoiceSpeech(
  text: string,
  speaker: string,
  settings: ModelSettings,
  accessToken: string,
): Promise<Blob> {
  let response = await requestCosyVoiceSpeech(text, speaker, settings, accessToken);
  if (response.status === 401) {
    const refreshedAccessToken = await getFreshAccessToken();
    if (refreshedAccessToken) {
      response = await requestCosyVoiceSpeech(text, speaker, settings, refreshedAccessToken);
    }
  }

  if (!response.ok) {
    throw new Error(await parseSpeechError(response));
  }

  return response.blob();
}

async function requestCosyVoiceSpeech(text: string, speaker: string, settings: ModelSettings, accessToken: string): Promise<Response> {
  return fetch(`${getApiBaseUrl()}/tts/cosyvoice/speech`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      text,
      speaker,
      api_url: settings.cosyvoiceBaseUrl,
    }),
  });
}

async function requestKokoroSpeech(text: string, voice: string, settings: ModelSettings, accessToken: string, options: TtsSynthesisOptions): Promise<Response> {
  return fetch(`${getApiBaseUrl()}/tts/kokoro/speech`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      text,
      voice,
      api_url: settings.ttsApiUrl,
      model: settings.ttsProvider,
      speed: options.speed,
    }),
  });
}

const AUDIO_PLAYBACK_TIMEOUT_MS = 30_000;

let sharedAudioContext: AudioContext | null = null;
let activeAudioSource: AudioBufferSourceNode | null = null;
let activeAudioTimeoutId: number | null = null;
let resolveActiveAudioPlayback: (() => void) | null = null;

export function unlockAudio(): void {
  if (!sharedAudioContext) {
    sharedAudioContext = new AudioContext();
  }
  if (sharedAudioContext.state === "suspended") {
    sharedAudioContext.resume();
  }
}

function getOrCreateAudioContext(): AudioContext {
  if (!sharedAudioContext || sharedAudioContext.state === "closed") {
    sharedAudioContext = new AudioContext();
  }
  return sharedAudioContext;
}

export function stopAudioPlayback(): void {
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  if (activeAudioTimeoutId !== null) {
    window.clearTimeout(activeAudioTimeoutId);
    activeAudioTimeoutId = null;
  }
  const source = activeAudioSource;
  activeAudioSource = null;
  if (source) {
    source.onended = null;
    try {
      source.stop();
    } catch {
      // The source may already have ended.
    }
    try {
      source.disconnect();
    } catch {
      // The source may already be disconnected.
    }
  }
  resolveActiveAudioPlayback?.();
  resolveActiveAudioPlayback = null;
}

export async function playAudioBlob(audioBlob: Blob, playbackRate = 1): Promise<void> {
  if (audioBlob.size <= 0) {
    throw new Error("TTS returned empty audio");
  }

  const audioContext = getOrCreateAudioContext();
  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }

  const arrayBuffer = await audioBlob.arrayBuffer();
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
  const source = audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.playbackRate.value = Math.max(0.25, Math.min(playbackRate, 4));
  source.connect(audioContext.destination);

  stopAudioPlayback();
  activeAudioSource = source;

  await new Promise<void>((resolve, reject) => {
    activeAudioTimeoutId = window.setTimeout(() => {
      activeAudioTimeoutId = null;
      if (activeAudioSource === source) {
        activeAudioSource = null;
        source.onended = null;
        try {
          source.stop();
        } catch {
          // The source may already have ended.
        }
        try {
          source.disconnect();
        } catch {
          // The source may already be disconnected.
        }
      }
      resolveActiveAudioPlayback = null;
      reject(new Error("Audio playback timed out"));
    }, AUDIO_PLAYBACK_TIMEOUT_MS);
    resolveActiveAudioPlayback = resolve;
    source.onended = () => {
      if (activeAudioSource !== source) {
        return;
      }
      if (activeAudioTimeoutId !== null) {
        window.clearTimeout(activeAudioTimeoutId);
        activeAudioTimeoutId = null;
      }
      activeAudioSource = null;
      resolveActiveAudioPlayback = null;
      resolve();
    };
    source.start();
  });
}

async function parseSpeechError(response: Response): Promise<string> {
  let message = `TTS failed: ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: string };
    message = body.detail ?? message;
  } catch {
    // Keep the status-based message when the response is not JSON.
  }
  return message;
}

export function getCachedAudioUrl(hashWithExt: string): string {
  return `${getApiBaseUrl()}/tts/cached/${hashWithExt}`;
}

export async function ensureCachedAudio(
  text: string,
  voice: string,
  speechRate: number,
  accessToken: string,
  suffix: string = "mp3",
): Promise<{ cached: boolean; url: string }> {
  const response = await fetch(`${getApiBaseUrl()}/tts/ensure-cached`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text, voice, speech_rate: speechRate, suffix }),
  });

  if (!response.ok) {
    throw new Error(await parseSpeechError(response));
  }

  return response.json() as Promise<{ cached: boolean; url: string }>;
}

export async function prefetchCourseAudio(
  courseId: string,
  voice: string,
  language: string,
  speechRate: number,
  accessToken: string,
): Promise<PrefetchCourseAudioResult> {
  const response = await fetch(`${getApiBaseUrl()}/tts/prefetch-course-audio`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      course_id: courseId,
      voice,
      language,
      speech_rate: speechRate,
    }),
  });

  if (!response.ok) {
    throw new Error(await parseSpeechError(response));
  }

  return response.json() as Promise<PrefetchCourseAudioResult>;
}

export async function getPhonicsDeck(accessToken: string): Promise<PhonicsDeck> {
  const response = await fetch(`${getApiBaseUrl()}/tts/phonics-deck`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(await parseSpeechError(response));
  }

  return response.json() as Promise<PhonicsDeck>;
}

export async function generatePhonicsDeckAudio(accessToken: string): Promise<{ generated: number; cached: number; errors: number; total: number }> {
  const response = await fetch(`${getApiBaseUrl()}/tts/phonics-deck/generate`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(await parseSpeechError(response));
  }

  return response.json() as Promise<{ generated: number; cached: number; errors: number; total: number }>;
}

export async function playCachedAudio(url: string, playbackRate = 1): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Cached audio not found: ${response.status}`);
  }
  const audioBlob = await response.blob();
  await playAudioBlob(audioBlob, playbackRate);
}

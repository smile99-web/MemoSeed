import { refreshAccessToken } from "@/lib/auth";
import { getApiBaseUrl } from "@/lib/api-base-url";
import { ModelSettings } from "@/lib/model-settings";

let refreshTokenPromise: Promise<string | null> | null = null;

export interface TtsSynthesisOptions {
  speechRate?: number;
  speed?: number;
}

async function getFreshAccessToken(): Promise<string | null> {
  refreshTokenPromise ??= refreshAccessToken().finally(() => {
    refreshTokenPromise = null;
  });
  return refreshTokenPromise;
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

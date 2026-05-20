import { refreshAccessToken } from "@/lib/auth";
import { ModelSettings } from "@/lib/model-settings";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
let refreshTokenPromise: Promise<string | null> | null = null;

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
): Promise<Blob> {
  let response = await requestVolcengineSpeech(text, voice, language, settings, accessToken);
  if (response.status === 401) {
    const refreshedAccessToken = await getFreshAccessToken();
    if (refreshedAccessToken) {
      response = await requestVolcengineSpeech(text, voice, language, settings, refreshedAccessToken);
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
): Promise<Response> {
  return fetch(`${apiBaseUrl}/tts/speech`, {
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
      app_id: settings.volcengineTtsAppId,
      access_token: settings.volcengineTtsAccessToken,
      secret_key: settings.volcengineTtsSecretKey,
      resource_id: settings.volcengineTtsResourceId,
      model: settings.volcengineTtsModel,
    }),
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

import { apiRequest } from "@/lib/api";
import { getAccessToken, refreshAccessToken } from "@/lib/auth";

export type ModelMode = "local" | "online";
export type LlmProvider = "ollama" | "deepseek";
export type TtsProvider = "kokoro" | "volcark";

export interface ModelSettings {
  modelMode: ModelMode;
  llmProvider: LlmProvider;
  llmBaseUrl: string;
  llmModel: string;
  llmApiKey: string;
  ttsProvider: TtsProvider;
  ttsApiUrl: string;
  ttsDocsUrl: string;
  ttsWebUrl: string;
  ttsEnglishVoice: string;
  ttsChineseVoice: string;
  volcengineTtsEndpoint: string;
  volcengineTtsAppId: string;
  volcengineTtsAccessToken: string;
  volcengineTtsSecretKey: string;
  volcengineTtsResourceId: string;
  volcengineTtsModel: string;
}

const modelSettingsKey = "memoseed_model_settings";

export const defaultModelSettings: ModelSettings = {
  modelMode: "local",
  llmProvider: "ollama",
  llmBaseUrl: "http://localhost:11434",
  llmModel: "phi4-mini",
  llmApiKey: "",
  ttsProvider: "kokoro",
  ttsApiUrl: "http://localhost:8880",
  ttsDocsUrl: "http://localhost:8880/docs",
  ttsWebUrl: "http://localhost:8880/web",
  ttsEnglishVoice: "af_heart",
  ttsChineseVoice: "zf_xiaobei",
  volcengineTtsEndpoint: "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
  volcengineTtsAppId: "",
  volcengineTtsAccessToken: "",
  volcengineTtsSecretKey: "",
  volcengineTtsResourceId: "seed-tts-2.0",
  volcengineTtsModel: "seed-tts-2.0-standard",
};

export const deepSeekModelSettings: Pick<ModelSettings, "llmProvider" | "llmBaseUrl" | "llmModel"> = {
  llmProvider: "deepseek",
  llmBaseUrl: "https://api.deepseek.com",
  llmModel: "deepseek-v4-flash",
};

export const volcarkTtsModelSettings: Pick<
  ModelSettings,
  "ttsProvider" | "ttsEnglishVoice" | "ttsChineseVoice" | "volcengineTtsResourceId" | "volcengineTtsModel"
> = {
  ttsProvider: "volcark",
  ttsEnglishVoice: "en_female_dacey_uranus_bigtts",
  ttsChineseVoice: "zh_female_xiaohe_uranus_bigtts",
  volcengineTtsResourceId: "seed-tts-2.0",
  volcengineTtsModel: "seed-tts-2.0-standard",
};

export const localModelModeSettings: Pick<
  ModelSettings,
  "modelMode" | "llmProvider" | "llmBaseUrl" | "llmModel" | "ttsProvider" | "ttsEnglishVoice" | "ttsChineseVoice"
> = {
  modelMode: "local",
  llmProvider: "ollama",
  llmBaseUrl: defaultModelSettings.llmBaseUrl,
  llmModel: defaultModelSettings.llmModel,
  ttsProvider: "kokoro",
  ttsEnglishVoice: defaultModelSettings.ttsEnglishVoice,
  ttsChineseVoice: defaultModelSettings.ttsChineseVoice,
};

export const onlineModelModeSettings: Pick<
  ModelSettings,
  | "modelMode"
  | "llmProvider"
  | "llmBaseUrl"
  | "llmModel"
  | "ttsProvider"
  | "ttsEnglishVoice"
  | "ttsChineseVoice"
  | "volcengineTtsResourceId"
  | "volcengineTtsModel"
> = {
  modelMode: "online",
  ...deepSeekModelSettings,
  ...volcarkTtsModelSettings,
};

export function getModelSettings(): ModelSettings {
  if (typeof window === "undefined") {
    return defaultModelSettings;
  }

  const storedSettings = window.localStorage.getItem(modelSettingsKey);
  if (!storedSettings) {
    return defaultModelSettings;
  }

  try {
    const parsedSettings = JSON.parse(storedSettings) as Partial<ModelSettings>;
    const llmProvider = parsedSettings.llmProvider === "deepseek" ? "deepseek" : "ollama";
    const ttsProvider = parsedSettings.ttsProvider === "volcark" ? "volcark" : "kokoro";
    const modelMode = parsedSettings.modelMode === "online" || llmProvider === "deepseek" || ttsProvider === "volcark" ? "online" : "local";
    return { ...defaultModelSettings, ...parsedSettings, modelMode, llmProvider, ttsProvider };
  } catch {
    window.localStorage.removeItem(modelSettingsKey);
    return defaultModelSettings;
  }
}

export function saveLocalModelSettings(settings: ModelSettings): void {
  window.localStorage.setItem(modelSettingsKey, JSON.stringify(settings));
}

export function saveModelSettings(settings: ModelSettings): void {
  saveLocalModelSettings(settings);
}

export async function loadPersistedModelSettings(accessToken = getAccessToken()): Promise<ModelSettings> {
  const localSettings = getModelSettings();
  if (!accessToken) {
    return localSettings;
  }

  try {
    const response = await apiRequest<{ settings: Partial<ModelSettings> }>("/settings/model", { accessToken });
    const settings = normalizeModelSettings(response.settings);
    saveLocalModelSettings(settings);
    return settings;
  } catch {
    const refreshedAccessToken = await refreshAccessToken();
    if (!refreshedAccessToken) {
      return localSettings;
    }

    try {
      const response = await apiRequest<{ settings: Partial<ModelSettings> }>("/settings/model", { accessToken: refreshedAccessToken });
      const settings = normalizeModelSettings(response.settings);
      saveLocalModelSettings(settings);
      return settings;
    } catch {
      return localSettings;
    }
  }
}

export async function savePersistedModelSettings(settings: ModelSettings, accessToken = getAccessToken()): Promise<void> {
  saveLocalModelSettings(settings);
  if (!accessToken) {
    return;
  }

  try {
    await apiRequest<{ settings: Partial<ModelSettings> }, { settings: ModelSettings }>("/settings/model", {
      method: "PUT",
      accessToken,
      body: { settings },
    });
  } catch {
    const refreshedAccessToken = await refreshAccessToken();
    if (!refreshedAccessToken) {
      return;
    }
    await apiRequest<{ settings: Partial<ModelSettings> }, { settings: ModelSettings }>("/settings/model", {
      method: "PUT",
      accessToken: refreshedAccessToken,
      body: { settings },
    });
  }
}

export function normalizeModelSettings(settings: Partial<ModelSettings>): ModelSettings {
  const mergedSettings = { ...defaultModelSettings, ...settings };
  const llmProvider: LlmProvider = mergedSettings.llmProvider === "deepseek" ? "deepseek" : "ollama";
  const ttsProvider: TtsProvider = mergedSettings.ttsProvider === "volcark" ? "volcark" : "kokoro";
  const modelMode: ModelMode = mergedSettings.modelMode === "online" || llmProvider === "deepseek" || ttsProvider === "volcark" ? "online" : "local";
  const normalizedSettings = { ...mergedSettings, modelMode, llmProvider, ttsProvider };
  if (modelMode === "online" && normalizedSettings.volcengineTtsResourceId === "seed-tts-2.0") {
    if (!normalizedSettings.ttsEnglishVoice.endsWith("_uranus_bigtts")) {
      normalizedSettings.ttsEnglishVoice = volcarkTtsModelSettings.ttsEnglishVoice;
    }
    if (!normalizedSettings.ttsChineseVoice.endsWith("_uranus_bigtts")) {
      normalizedSettings.ttsChineseVoice = volcarkTtsModelSettings.ttsChineseVoice;
    }
  }
  return normalizedSettings;
}

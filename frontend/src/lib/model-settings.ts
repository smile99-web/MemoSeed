import { ApiRequestError, apiRequest } from "@/lib/api";
import { getAccessToken, refreshAccessToken } from "@/lib/auth";

export type ModelMode = "local" | "online";
export type LlmProvider = "ollama" | "deepseek" | "qwen";
export type TtsProvider = "kokoro" | "volcark" | "cosyvoice";

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
  ttsSpeedPreference: number;
  volcengineTtsEndpoint: string;
  volcengineTtsApiKey: string;
  volcengineTtsResourceId: string;
  volcengineTtsModel: string;
  cosyvoiceBaseUrl: string;
  cosyvoiceEnglishSpeaker: string;
  cosyvoiceChineseSpeaker: string;
  llmApiKeyConfigured?: boolean;
  volcengineTtsApiKeyConfigured?: boolean;
}

type PublicModelSettings = Partial<ModelSettings>;

export interface SaveModelSettingsResult {
  synced: boolean;
  error?: string;
}

const modelSettingsKey = "memoseed_model_settings";

export const defaultModelSettings: ModelSettings = {
  modelMode: "local",
  llmProvider: "ollama",
  llmBaseUrl: "http://localhost:11434",
  llmModel: "ali6parmak/hy-mt1.5:latest",
  llmApiKey: "",
  ttsProvider: "kokoro",
  ttsApiUrl: "http://localhost:8880",
  ttsDocsUrl: "http://localhost:8880/docs",
  ttsWebUrl: "http://localhost:8880/web",
  ttsEnglishVoice: "af_heart",
  ttsChineseVoice: "zf_xiaobei",
  ttsSpeedPreference: 1,
  volcengineTtsEndpoint: "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
  volcengineTtsApiKey: "",
  volcengineTtsResourceId: "seed-tts-2.0",
  volcengineTtsModel: "seed-tts-2.0-standard",
  cosyvoiceBaseUrl: "",
  cosyvoiceEnglishSpeaker: "英文女",
  cosyvoiceChineseSpeaker: "中文女",
};

export const deepSeekModelSettings: Pick<ModelSettings, "llmProvider" | "llmBaseUrl" | "llmModel"> = {
  llmProvider: "deepseek",
  llmBaseUrl: "https://api.deepseek.com",
  llmModel: "deepseek-v4-flash",
};

export const qwenModelSettings: Pick<ModelSettings, "llmProvider" | "llmBaseUrl" | "llmModel"> = {
  llmProvider: "qwen",
  llmBaseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  llmModel: "qwen3.6-flash",
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

export const cosyvoiceTtsModelSettings: Pick<
  ModelSettings,
  "ttsProvider" | "ttsEnglishVoice" | "ttsChineseVoice" | "cosyvoiceEnglishSpeaker" | "cosyvoiceChineseSpeaker"
> = {
  ttsProvider: "cosyvoice",
  ttsEnglishVoice: "英文女",
  ttsChineseVoice: "中文女",
  cosyvoiceEnglishSpeaker: "英文女",
  cosyvoiceChineseSpeaker: "中文女",
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
    const llmProvider = normalizeLlmProvider(parsedSettings.llmProvider);
    const ttsProvider = parsedSettings.ttsProvider === "volcark" ? "volcark" : parsedSettings.ttsProvider === "cosyvoice" ? "cosyvoice" : "kokoro";
    const modelMode = parsedSettings.modelMode === "online" ? "online" : "local";
    return { ...defaultModelSettings, ...parsedSettings, modelMode, llmProvider, ttsProvider };
  } catch {
    window.localStorage.removeItem(modelSettingsKey);
    return defaultModelSettings;
  }
}

export function saveLocalModelSettings(settings: ModelSettings): void {
  window.localStorage.setItem(modelSettingsKey, JSON.stringify(stripSecretSettings(settings)));
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
    const response = await apiRequest<{ settings: PublicModelSettings }>("/settings/model", { accessToken });
    const settings = normalizeModelSettings({ ...localSettings, ...response.settings, llmApiKey: "", volcengineTtsApiKey: "" });
    saveLocalModelSettings(settings);
    return settings;
  } catch (error) {
    if (!isUnauthorizedError(error)) {
      return localSettings;
    }
    const refreshedAccessToken = await refreshAccessToken();
    if (!refreshedAccessToken) {
      return localSettings;
    }

    try {
      const response = await apiRequest<{ settings: PublicModelSettings }>("/settings/model", { accessToken: refreshedAccessToken });
      const settings = normalizeModelSettings({ ...localSettings, ...response.settings, llmApiKey: "", volcengineTtsApiKey: "" });
      saveLocalModelSettings(settings);
      return settings;
    } catch {
      return localSettings;
    }
  }
}

export async function savePersistedModelSettings(settings: ModelSettings, accessToken = getAccessToken()): Promise<SaveModelSettingsResult> {
  saveLocalModelSettings(settings);
  if (!accessToken) {
    return { synced: false };
  }

  try {
    await apiRequest<{ settings: PublicModelSettings }, { settings: ModelSettings }>("/settings/model", {
      method: "PUT",
      accessToken,
      body: { settings },
    });
    return { synced: true };
  } catch (error) {
    if (!isUnauthorizedError(error)) {
      return { synced: false, error: error instanceof Error ? error.message : "服务器同步失败" };
    }
    const refreshedAccessToken = await refreshAccessToken();
    if (!refreshedAccessToken) {
      return { synced: false, error: error instanceof Error ? error.message : "服务器同步失败" };
    }
    try {
      await apiRequest<{ settings: PublicModelSettings }, { settings: ModelSettings }>("/settings/model", {
        method: "PUT",
        accessToken: refreshedAccessToken,
        body: { settings },
      });
      return { synced: true };
    } catch (retryError) {
      return { synced: false, error: retryError instanceof Error ? retryError.message : "服务器同步失败" };
    }
  }
}

function stripSecretSettings(settings: ModelSettings): ModelSettings {
  return { ...settings, llmApiKey: "", volcengineTtsApiKey: "" };
}

function isUnauthorizedError(error: unknown): boolean {
  return error instanceof ApiRequestError && error.status === 401;
}

function normalizeLlmProvider(provider: unknown): LlmProvider {
  return provider === "deepseek" || provider === "qwen" ? provider : "ollama";
}

export function normalizeModelSettings(settings: Partial<ModelSettings>): ModelSettings {
  const mergedSettings = { ...defaultModelSettings, ...settings };
  const llmProvider = normalizeLlmProvider(mergedSettings.llmProvider);
  const ttsProvider: TtsProvider = mergedSettings.ttsProvider === "volcark" ? "volcark" : mergedSettings.ttsProvider === "cosyvoice" ? "cosyvoice" : "kokoro";
  const modelMode: ModelMode = mergedSettings.modelMode === "online" ? "online" : "local";
  const normalizedSettings = { ...mergedSettings, modelMode, llmProvider, ttsProvider };
  if (ttsProvider === "volcark" && normalizedSettings.volcengineTtsResourceId === "seed-tts-2.0") {
    if (!normalizedSettings.ttsEnglishVoice.endsWith("_uranus_bigtts")) {
      normalizedSettings.ttsEnglishVoice = volcarkTtsModelSettings.ttsEnglishVoice;
    }
    if (!normalizedSettings.ttsChineseVoice.endsWith("_uranus_bigtts")) {
      normalizedSettings.ttsChineseVoice = volcarkTtsModelSettings.ttsChineseVoice;
    }
  }
  return normalizedSettings;
}

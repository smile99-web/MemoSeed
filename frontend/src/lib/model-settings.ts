export interface ModelSettings {
  llmProvider: string;
  llmBaseUrl: string;
  llmModel: string;
  ttsProvider: string;
  ttsApiUrl: string;
  ttsDocsUrl: string;
  ttsWebUrl: string;
  ttsEnglishVoice: string;
  ttsChineseVoice: string;
}

const modelSettingsKey = "memoseed_model_settings";

export const defaultModelSettings: ModelSettings = {
  llmProvider: "ollama",
  llmBaseUrl: "http://localhost:11434",
  llmModel: "phi4-mini",
  ttsProvider: "kokoro",
  ttsApiUrl: "http://localhost:8880",
  ttsDocsUrl: "http://localhost:8880/docs",
  ttsWebUrl: "http://localhost:8880/web",
  ttsEnglishVoice: "af_heart",
  ttsChineseVoice: "zf_xiaobei",
};

export function getModelSettings(): ModelSettings {
  const storedSettings = window.localStorage.getItem(modelSettingsKey);
  if (!storedSettings) {
    return defaultModelSettings;
  }

  try {
    return { ...defaultModelSettings, ...(JSON.parse(storedSettings) as Partial<ModelSettings>) };
  } catch {
    window.localStorage.removeItem(modelSettingsKey);
    return defaultModelSettings;
  }
}

export function saveModelSettings(settings: ModelSettings): void {
  window.localStorage.setItem(modelSettingsKey, JSON.stringify(settings));
}

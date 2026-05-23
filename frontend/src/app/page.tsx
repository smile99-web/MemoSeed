"use client";

import { Brain, CalendarClock, GraduationCap, Repeat2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AuthUser, clearAuthSession, getAccessToken, getAuthUser, isAuthenticated } from "@/lib/auth";
import { translateLearningText } from "@/lib/learning";
import {
  ModelSettings,
  defaultModelSettings,
  getModelSettings,
  loadPersistedModelSettings,
  deepSeekModelSettings,
  qwenModelSettings,
  volcarkTtsModelSettings,
  cosyvoiceTtsModelSettings,
  savePersistedModelSettings,
} from "@/lib/model-settings";
import { playAudioBlob, synthesizeCosyVoiceSpeech, synthesizeKokoroSpeech, synthesizeVolcengineSpeech, unlockAudio } from "@/lib/tts";

const kokoroEnglishVoices = [
  "af_heart",
  "af_bella",
  "af_sarah",
  "am_adam",
  "am_michael",
  "bf_emma",
  "bf_isabella",
  "bm_george",
  "bm_lewis",
] as const;

const kokoroChineseVoices = [
  "zf_xiaobei",
  "zf_xiaoni",
  "zf_xiaoxiao",
  "zf_xiaoyi",
  "zm_yunjian",
  "zm_yunxi",
  "zm_yunxia",
  "zm_yunyang",
] as const;

const volcengineEnglishVoices = [
  { label: "Dacey（美式女声）", value: "en_female_dacey_uranus_bigtts" },
  { label: "Tim（美式男声）", value: "en_male_tim_uranus_bigtts" },
  { label: "Stokie（美式女声）", value: "en_female_stokie_uranus_bigtts" },
  { label: "Vivi 2.0（中英双语女声）", value: "zh_female_vv_uranus_bigtts" },
  { label: "云舟 2.0（中英混合男声）", value: "zh_male_m191_uranus_bigtts" },
  { label: "魅力苏菲 2.0（中英混合男声）", value: "zh_male_sophie_uranus_bigtts" },
] as const;

const volcengineChineseVoices = [
  { label: "小何 2.0（通用女声）", value: "zh_female_xiaohe_uranus_bigtts" },
  { label: "Vivi 2.0（多语种女声）", value: "zh_female_vv_uranus_bigtts" },
  { label: "云舟 2.0（通用男声）", value: "zh_male_m191_uranus_bigtts" },
  { label: "小天 2.0（通用男声）", value: "zh_male_taocheng_uranus_bigtts" },
  { label: "刘飞 2.0（通用男声）", value: "zh_male_liufei_uranus_bigtts" },
  { label: "魅力苏菲 2.0（通用男声）", value: "zh_male_sophie_uranus_bigtts" },
  { label: "知性灿灿 2.0（角色扮演女声）", value: "zh_female_cancan_uranus_bigtts" },
  { label: "清新女声 2.0", value: "zh_female_qingxinnvsheng_uranus_bigtts" },
  { label: "爽快思思 2.0（爽朗女声）", value: "zh_female_shuangkuaisisi_uranus_bigtts" },
  { label: "邻家女孩 2.0（亲切女声）", value: "zh_female_linjianvhai_uranus_bigtts" },
  { label: "魅力女友 2.0（温柔女声）", value: "zh_female_meilinvyou_uranus_bigtts" },
  { label: "撒娇学妹 2.0（可爱女声）", value: "zh_female_sajiaoxuemei_uranus_bigtts" },
  { label: "大壹 2.0（视频配音男声）", value: "zh_male_dayi_uranus_bigtts" },
  { label: "猴哥 2.0（角色男声）", value: "zh_male_sunwukong_uranus_bigtts" },
  { label: "儒雅逸辰 2.0（书卷男声）", value: "zh_male_ruyayichen_uranus_bigtts" },
] as const;

const cosyvoiceSpeakers = [
  { label: "中文女", value: "中文女" },
  { label: "中文男", value: "中文男" },
  { label: "英文女", value: "英文女" },
  { label: "英文男", value: "英文男" },
  { label: "粤语女", value: "粤语女" },
  { label: "日语男", value: "日语男" },
  { label: "韩语女", value: "韩语女" },
] as const;

type ModelSettingsTextField = Extract<{
  [Key in keyof ModelSettings]: ModelSettings[Key] extends string ? Key : never;
}[keyof ModelSettings], string>;

const savedSecretPlaceholder = "••••••••（已保存，重新输入可覆盖）";

const workflowCards = [
  {
    title: "热身复习",
    description: "昨天错题、高遗忘风险、即将到期内容。",
    minutes: "10 分钟",
    icon: Repeat2,
  },
  {
    title: "新内容学习",
    description: "新单词与常用短语，控制每日认知负荷。",
    minutes: "20 分钟",
    icon: GraduationCap,
  },
  {
    title: "句子训练",
    description: "中译英、拼写、填空，强化主动回忆。",
    minutes: "20 分钟",
    icon: Brain,
  },
  {
    title: "错题强化",
    description: "今日错误当天再次出现，拼写重新输入。",
    minutes: "10 分钟",
    icon: CalendarClock,
  },
] as const;

export default function HomePage() {
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [hasLoadedSession, setHasLoadedSession] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [isTestingLocalModels, setIsTestingLocalModels] = useState(false);
  const [isTestingNetworkModels, setIsTestingNetworkModels] = useState(false);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);

  useEffect(() => {
    const user = isAuthenticated() ? getAuthUser() : null;
    setCurrentUser(user);
    setModelSettings(getModelSettings());
    setHasLoadedSession(true);
    if (user) {
      void loadPersistedModelSettings().then(setModelSettings);
    }
  }, []);

  const isLoggedIn = Boolean(currentUser);

  function handleLogout() {
    clearAuthSession();
    setCurrentUser(null);
  }

  function handleModelSettingsChange(field: ModelSettingsTextField, value: string) {
    setModelSettings((current) => ({ ...current, [field]: value }));
  }

  function getLlmProviderLabel() {
    if (modelSettings.llmProvider === "deepseek") {
      return "DeepSeek";
    }
    if (modelSettings.llmProvider === "qwen") {
      return "Qwen";
    }
    return "Ollama";
  }

  function handleLlmProviderChange(provider: ModelSettings["llmProvider"]) {
    setSettingsMessage(null);
    setModelSettings((current) => ({
      ...current,
      ...(provider === "deepseek" ? deepSeekModelSettings : provider === "qwen" ? qwenModelSettings : {
        modelMode: "local",
        llmProvider: "ollama",
        llmBaseUrl: defaultModelSettings.llmBaseUrl,
        llmModel: defaultModelSettings.llmModel,
      }),
    }));
  }

  function handleTtsProviderChange(provider: ModelSettings["ttsProvider"]) {
    setSettingsMessage(null);
    setModelSettings((current) => ({
      ...current,
      ...(provider === "volcark" ? volcarkTtsModelSettings : provider === "cosyvoice" ? cosyvoiceTtsModelSettings : {
        ttsProvider: "kokoro",
        ttsEnglishVoice: defaultModelSettings.ttsEnglishVoice,
        ttsChineseVoice: defaultModelSettings.ttsChineseVoice,
      }),
    }));
  }

  async function handleSaveModelSettings() {
    setIsSavingSettings(true);
    setSettingsMessage(null);
    try {
      const result = await savePersistedModelSettings(modelSettings);
      if (result.synced) {
        setModelSettings((current) => ({
          ...current,
          llmApiKey: "",
          volcengineTtsApiKey: "",
          llmApiKeyConfigured: current.llmApiKey.trim() ? true : current.llmApiKeyConfigured,
          volcengineTtsApiKeyConfigured: current.volcengineTtsApiKey.trim()
            ? true
            : current.volcengineTtsApiKeyConfigured,
        }));
        setSettingsMessage("模型设置已保存。");
        setIsSettingsOpen(false);
        return;
      }
      setSettingsMessage(result.error ? `模型设置已保存到本地；服务器同步失败：${result.error}` : "模型设置已保存到本地。");
    } catch (error) {
      setSettingsMessage(error instanceof Error ? error.message : "模型设置保存失败");
    } finally {
      setIsSavingSettings(false);
    }
  }

  async function handleTestLocalModels() {
    unlockAudio();
    const accessToken = getAccessToken();
    if (!accessToken) {
      setSettingsMessage("请先登录后再测试本地模型。");
      return;
    }

    setIsTestingLocalModels(true);
    setSettingsMessage("正在测试本地模型...");
    const results: string[] = [];

    if (modelSettings.llmProvider === "ollama") {
      try {
        await translateLearningText("hello", accessToken, { ...modelSettings, modelMode: "local", llmProvider: "ollama" });
        results.push("Ollama：连通正常");
      } catch (error) {
        results.push(`Ollama：${error instanceof Error ? error.message : "测试失败"}`);
      }
    }

    if (modelSettings.ttsProvider === "kokoro") {
      try {
        const audio = await synthesizeKokoroSpeech("你好，这是本地 Kokoro TTS 中文音色测试。", modelSettings.ttsChineseVoice, { ...modelSettings, modelMode: "local", ttsProvider: "kokoro" }, accessToken);
        if (audio.size > 0) {
          await playAudioBlob(audio);
          results.push("Kokoro TTS：连通正常");
        } else {
          results.push("Kokoro TTS：返回音频为空");
        }
      } catch (error) {
        results.push(`Kokoro TTS：${error instanceof Error ? error.message : "测试失败"}`);
      }
    }

    if (modelSettings.ttsProvider === "cosyvoice") {
      try {
        const audio = await synthesizeCosyVoiceSpeech("你好，这是本地 CosyVoice 2.0 中文音色测试。", modelSettings.cosyvoiceChineseSpeaker, { ...modelSettings, modelMode: "local", ttsProvider: "cosyvoice" }, accessToken);
        if (audio.size > 0) {
          await playAudioBlob(audio);
          results.push("CosyVoice 2.0 TTS：连通正常");
        } else {
          results.push("CosyVoice 2.0 TTS：返回音频为空");
        }
      } catch (error) {
        results.push(`CosyVoice 2.0 TTS：${error instanceof Error ? error.message : "测试失败"}`);
      }
    }

    setIsTestingLocalModels(false);
    setSettingsMessage(results.join("；"));
  }

  async function handleTestNetworkModels() {
    unlockAudio();
    const accessToken = getAccessToken();
    if (!accessToken) {
      setSettingsMessage("请先登录后再测试网络模型。");
      return;
    }

    setIsTestingNetworkModels(true);
    setSettingsMessage("正在测试网络模型...");
    const results: string[] = [];

    if (modelSettings.llmProvider !== "ollama") {
      const providerLabel = getLlmProviderLabel();
      try {
        await translateLearningText("hello", accessToken, { ...modelSettings, modelMode: "online" });
        results.push(`${providerLabel}：连通正常`);
      } catch (error) {
        results.push(`${providerLabel}：${error instanceof Error ? error.message : "测试失败"}`);
      }
    }

    if (modelSettings.ttsProvider === "volcark") {
      try {
        const audio = await synthesizeVolcengineSpeech("你好，正在测试中文音色。", modelSettings.ttsChineseVoice, "zh-CN", { ...modelSettings, modelMode: "online", ttsProvider: "volcark" }, accessToken);
        if (audio.size > 0) {
          await playAudioBlob(audio);
          results.push(`火山方舟中文音色：连通正常（${modelSettings.ttsChineseVoice}）`);
        } else {
          results.push(`火山方舟中文音色：返回音频为空（${modelSettings.ttsChineseVoice}）`);
        }
      } catch (error) {
        results.push(`火山方舟中文音色（${modelSettings.ttsChineseVoice}）：${error instanceof Error ? error.message : "测试失败"}`);
      }

      try {
        const audio = await synthesizeVolcengineSpeech("Hello, this is an English voice test.", modelSettings.ttsEnglishVoice, "en-US", { ...modelSettings, modelMode: "online", ttsProvider: "volcark" }, accessToken);
        if (audio.size > 0) {
          await playAudioBlob(audio);
          results.push(`火山方舟英文音色：连通正常（${modelSettings.ttsEnglishVoice}）`);
        } else {
          results.push(`火山方舟英文音色：返回音频为空（${modelSettings.ttsEnglishVoice}）`);
        }
      } catch (error) {
        results.push(`火山方舟英文音色（${modelSettings.ttsEnglishVoice}）：${error instanceof Error ? error.message : "测试失败"}`);
      }
    }

    setIsTestingNetworkModels(false);
    setSettingsMessage(results.join("；"));
  }

  return (
    <main className="min-h-screen px-6 py-10 ipad:px-8 ipad:py-14">
      {isSettingsOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-6 py-8">
          <div className="max-h-full w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-2xl font-bold tracking-tight ipad:text-3xl">模型设置</h2>
                <p className="mt-2 text-sm text-muted-foreground ipad:text-base">配置用于例句生成、英中翻译和英语发音的模型服务。</p>
              </div>
              <Button onClick={() => setIsSettingsOpen(false)} type="button" variant="outline">
                关闭
              </Button>
            </div>

            <div className="mt-6 grid gap-5 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="llm-provider">LLM 模型来源</label>
                <select
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="llm-provider"
                  value={modelSettings.llmProvider}
                  onChange={(event) => handleLlmProviderChange(event.target.value as ModelSettings["llmProvider"])}
                >
                  <option value="ollama">本地 Ollama</option>
                  <option value="deepseek">网络 DeepSeek</option>
                  <option value="qwen">网络 Qwen</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="tts-provider">TTS 语音来源</label>
                <select
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="tts-provider"
                  value={modelSettings.ttsProvider}
                  onChange={(event) => handleTtsProviderChange(event.target.value as ModelSettings["ttsProvider"])}
                >
                  <option value="kokoro">本地 Kokoro</option>
                  <option value="cosyvoice">本地 CosyVoice 2.0</option>
                  <option value="volcark">网络火山方舟</option>
                </select>
              </div>
              <div className="border-t pt-5 md:col-span-2">
                <h3 className="text-base font-semibold">
                  LLM 模型（{getLlmProviderLabel()}）
                </h3>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="llm-model">LLM 模型</label>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="llm-model"
                  value={modelSettings.llmModel}
                  onChange={(event) => handleModelSettingsChange("llmModel", event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="llm-base-url">
                  {modelSettings.llmProvider !== "ollama" ? "OpenAI Base URL" : "Ollama 调用地址"}
                </label>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="llm-base-url"
                  value={modelSettings.llmBaseUrl}
                  onChange={(event) => handleModelSettingsChange("llmBaseUrl", event.target.value)}
                />
              </div>
              {modelSettings.llmProvider !== "ollama" ? (
                <div className="space-y-2 md:col-span-2">
                  <label className="text-sm font-medium" htmlFor="llm-api-key">{getLlmProviderLabel()} API Key</label>
                  <input
                    autoComplete="off"
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="llm-api-key"
                    placeholder={modelSettings.llmApiKeyConfigured ? savedSecretPlaceholder : "稍后填写"}
                    type="password"
                    value={modelSettings.llmApiKey}
                    onChange={(event) => handleModelSettingsChange("llmApiKey", event.target.value)}
                  />
                </div>
              ) : null}

              <div className="border-t pt-5 md:col-span-2">
                <h3 className="text-base font-semibold">
                  TTS 模型（{modelSettings.ttsProvider === "volcark" ? "火山方舟" : modelSettings.ttsProvider === "cosyvoice" ? "CosyVoice 2.0" : "Kokoro"}）
                </h3>
              </div>
              {modelSettings.ttsProvider === "kokoro" ? (
                <div className="space-y-2 md:col-span-2">
                  <label className="text-sm font-medium" htmlFor="tts-api-url">Kokoro API 地址</label>
                  <input
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-api-url"
                    value={modelSettings.ttsApiUrl}
                    onChange={(event) => handleModelSettingsChange("ttsApiUrl", event.target.value)}
                  />
                </div>
              ) : modelSettings.ttsProvider === "cosyvoice" ? (
                <div className="space-y-2 md:col-span-2">
                  <label className="text-sm font-medium" htmlFor="cosyvoice-base-url">CosyVoice API 地址</label>
                  <input
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="cosyvoice-base-url"
                    placeholder="http://cosyvoice:50000（留空使用默认）"
                    value={modelSettings.cosyvoiceBaseUrl}
                    onChange={(event) => handleModelSettingsChange("cosyvoiceBaseUrl", event.target.value)}
                  />
                </div>
              ) : (
                <>
                  <div className="space-y-2 md:col-span-2">
                    <label className="text-sm font-medium" htmlFor="volcengine-tts-api-key">X-Api-Key</label>
                    <input
                      autoComplete="off"
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="volcengine-tts-api-key"
                      placeholder={modelSettings.volcengineTtsApiKeyConfigured ? savedSecretPlaceholder : "稍后填写"}
                      type="password"
                      value={modelSettings.volcengineTtsApiKey}
                      onChange={(event) => handleModelSettingsChange("volcengineTtsApiKey", event.target.value)}
                    />
                  </div>
                  <div className="space-y-2 md:col-span-2">
                    <label className="text-sm font-medium" htmlFor="volcengine-tts-endpoint">接口地址</label>
                    <input
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="volcengine-tts-endpoint"
                      value={modelSettings.volcengineTtsEndpoint}
                      onChange={(event) => handleModelSettingsChange("volcengineTtsEndpoint", event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor="volcengine-tts-resource-id">资源 ID</label>
                    <input
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="volcengine-tts-resource-id"
                      value={modelSettings.volcengineTtsResourceId}
                      onChange={(event) => handleModelSettingsChange("volcengineTtsResourceId", event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor="volcengine-tts-model">模型</label>
                    <input
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="volcengine-tts-model"
                      value={modelSettings.volcengineTtsModel}
                      onChange={(event) => handleModelSettingsChange("volcengineTtsModel", event.target.value)}
                    />
                  </div>
                </>
              )}
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="tts-english-voice">英文语音音色</label>
                {modelSettings.ttsProvider === "kokoro" ? (
                  <select
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-english-voice"
                    value={modelSettings.ttsEnglishVoice}
                    onChange={(event) => handleModelSettingsChange("ttsEnglishVoice", event.target.value)}
                  >
                    {kokoroEnglishVoices.map((voice) => (
                      <option key={voice} value={voice}>{voice}</option>
                    ))}
                  </select>
                ) : modelSettings.ttsProvider === "cosyvoice" ? (
                  <select
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-english-voice"
                    value={modelSettings.ttsEnglishVoice}
                    onChange={(event) => {
                      handleModelSettingsChange("ttsEnglishVoice", event.target.value);
                      handleModelSettingsChange("cosyvoiceEnglishSpeaker", event.target.value);
                    }}
                  >
                    {cosyvoiceSpeakers.map((speaker) => (
                      <option key={speaker.value} value={speaker.value}>{speaker.label}</option>
                    ))}
                  </select>
                ) : (
                  <select
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-english-voice"
                    value={modelSettings.ttsEnglishVoice}
                    onChange={(event) => handleModelSettingsChange("ttsEnglishVoice", event.target.value)}
                  >
                    {volcengineEnglishVoices.map((voice) => (
                      <option key={voice.value} value={voice.value}>{voice.label}</option>
                    ))}
                  </select>
                )}
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="tts-chinese-voice">中文语音音色</label>
                {modelSettings.ttsProvider === "kokoro" ? (
                  <select
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-chinese-voice"
                    value={modelSettings.ttsChineseVoice}
                    onChange={(event) => handleModelSettingsChange("ttsChineseVoice", event.target.value)}
                  >
                    {kokoroChineseVoices.map((voice) => (
                      <option key={voice} value={voice}>{voice}</option>
                    ))}
                  </select>
                ) : modelSettings.ttsProvider === "cosyvoice" ? (
                  <select
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-chinese-voice"
                    value={modelSettings.ttsChineseVoice}
                    onChange={(event) => {
                      handleModelSettingsChange("ttsChineseVoice", event.target.value);
                      handleModelSettingsChange("cosyvoiceChineseSpeaker", event.target.value);
                    }}
                  >
                    {cosyvoiceSpeakers.map((speaker) => (
                      <option key={speaker.value} value={speaker.value}>{speaker.label}</option>
                    ))}
                  </select>
                ) : (
                  <select
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-chinese-voice"
                    value={modelSettings.ttsChineseVoice}
                    onChange={(event) => handleModelSettingsChange("ttsChineseVoice", event.target.value)}
                  >
                    {volcengineChineseVoices.map((voice) => (
                      <option key={voice.value} value={voice.value}>{voice.label}</option>
                    ))}
                  </select>
                )}
              </div>
              {modelSettings.ttsProvider === "kokoro" ? (
                <>
                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor="tts-docs-url">Kokoro API 文档</label>
                    <input
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="tts-docs-url"
                      value={modelSettings.ttsDocsUrl}
                      onChange={(event) => handleModelSettingsChange("ttsDocsUrl", event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor="tts-web-url">Kokoro Web UI</label>
                    <input
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="tts-web-url"
                      value={modelSettings.ttsWebUrl}
                      onChange={(event) => handleModelSettingsChange("ttsWebUrl", event.target.value)}
                    />
                  </div>
                </>
              ) : null}
              {modelSettings.ttsProvider === "cosyvoice" ? (
                <div className="space-y-2 md:col-span-2">
                  <p className="text-xs text-muted-foreground">
                    CosyVoice 2.0 本地 TTS 模型。请确保 Docker 容器已启动，默认端口 50000。支持预训练音色：中文女、中文男、英文女、英文男。
                  </p>
                </div>
              ) : null}
            </div>

            <div className="mt-6 flex flex-wrap justify-end gap-3">
              <Button onClick={() => setModelSettings(defaultModelSettings)} type="button" variant="outline">
                恢复默认
              </Button>
              {modelSettings.llmProvider === "ollama" || modelSettings.ttsProvider === "kokoro" || modelSettings.ttsProvider === "cosyvoice" ? (
                <Button disabled={isTestingLocalModels} onClick={handleTestLocalModels} type="button" variant="secondary">
                  {isTestingLocalModels ? "测试中..." : "测试本地模型"}
                </Button>
              ) : null}
              {modelSettings.llmProvider !== "ollama" || modelSettings.ttsProvider === "volcark" ? (
                <Button disabled={isTestingNetworkModels} onClick={handleTestNetworkModels} type="button" variant="secondary">
                  {isTestingNetworkModels ? "测试中..." : "测试网络模型"}
                </Button>
              ) : null}
              <Button disabled={isSavingSettings} onClick={handleSaveModelSettings} type="button">
                {isSavingSettings ? "保存中..." : "保存设置"}
              </Button>
            </div>
            {settingsMessage ? <p className="mt-3 text-right text-sm text-muted-foreground">{settingsMessage}</p> : null}
          </div>
        </div>
      ) : null}

      <div className="mx-auto mb-6 max-w-6xl text-sm text-muted-foreground">
        {hasLoadedSession ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-white px-4 py-3 shadow-sm">
            {currentUser ? (
              <div>
                <p className="font-medium text-foreground">已登录：{currentUser.username}</p>
                <p>{currentUser.email}</p>
              </div>
            ) : (
              <div>
                <p className="font-medium text-foreground">未登录</p>
                <p>登录后可进入课程目录和学习内容。</p>
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setIsSettingsOpen(true)} type="button" variant="secondary">
                设置
              </Button>
              {currentUser ? (
                <Button onClick={handleLogout} type="button" variant="outline">
                  退出登录
                </Button>
              ) : (
                <>
                  <Button asChild>
                    <Link href="/login">账户登录</Link>
                  </Button>
                  <Button asChild variant="outline">
                    <Link href="/register">创建账号</Link>
                  </Button>
                </>
              )}
            </div>
          </div>
        ) : null}
      </div>

      <section className="mx-auto flex max-w-6xl flex-col gap-10">
        <div className="rounded-3xl bg-white p-8 shadow-sm md:p-12 ipad:p-10">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-primary ipad:text-base">MemoSeed</p>
          <h1 className="mt-4 max-w-3xl text-4xl font-bold tracking-tight md:text-6xl ipad:text-5xl">
            英语记忆种子
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground ipad:text-xl ipad:leading-9">
            基于艾宾浩斯记忆曲线、FSRS算法推荐 和 AI 动态调度，专注中小学阶段英语基础薄弱学生的单词、短语与简单句长期记忆。
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild>
              <Link href={isLoggedIn ? "/learning" : "/login"}>开始学习</Link>
            </Button>
            {!isLoggedIn ? (
              <Button asChild variant="outline">
                <Link href="/register">创建账号</Link>
              </Button>
            ) : null}
            <Button asChild variant="secondary">
              <Link href="/learning/import">导入/查看内容</Link>
            </Button>
            {isLoggedIn ? (
              <Button asChild variant="outline">
                <Link href="/dashboard">学习数据看板</Link>
              </Button>
            ) : null}
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 ipad:grid-cols-2">
          {workflowCards.map((card) => {
            const Icon = card.icon;
            return (
              <Card key={card.title}>
                <CardHeader>
                  <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary ipad:h-12 ipad:w-12">
                    <Icon className="h-5 w-5 ipad:h-6 ipad:w-6" />
                  </div>
                  <CardTitle className="ipad:text-xl">{card.title}</CardTitle>
                  <CardDescription className="ipad:text-base">{card.minutes}</CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-sm leading-6 text-muted-foreground ipad:text-base">{card.description}</p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>
    </main>
  );
}

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
  localModelModeSettings,
  onlineModelModeSettings,
  savePersistedModelSettings,
} from "@/lib/model-settings";
import { synthesizeVolcengineSpeech } from "@/lib/tts";

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
] as const;

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

  function handleModelSettingsChange(field: keyof ModelSettings, value: string) {
    setModelSettings((current) => ({ ...current, [field]: value }));
  }

  function handleModelModeChange(mode: ModelSettings["modelMode"]) {
    setSettingsMessage(null);
    setModelSettings((current) => ({ ...current, ...(mode === "online" ? onlineModelModeSettings : localModelModeSettings) }));
  }

  async function handleSaveModelSettings() {
    setIsSavingSettings(true);
    setSettingsMessage(null);
    try {
      await savePersistedModelSettings(modelSettings);
      setSettingsMessage("模型设置已保存。");
      setIsSettingsOpen(false);
    } catch (error) {
      setSettingsMessage(error instanceof Error ? error.message : "模型设置保存失败");
    } finally {
      setIsSavingSettings(false);
    }
  }

  async function handleTestNetworkModels() {
    const accessToken = getAccessToken();
    if (!accessToken) {
      setSettingsMessage("请先登录后再测试网络模型。");
      return;
    }

    setIsTestingNetworkModels(true);
    setSettingsMessage("正在测试网络模型...");
    const results: string[] = [];

    try {
      await translateLearningText("hello", accessToken, { ...modelSettings, modelMode: "online", llmProvider: "deepseek" });
      results.push("DeepSeek：连通正常");
    } catch (error) {
      results.push(`DeepSeek：${error instanceof Error ? error.message : "测试失败"}`);
    }

    try {
      const audio = await synthesizeVolcengineSpeech("你好", modelSettings.ttsChineseVoice, "zh-CN", { ...modelSettings, modelMode: "online", ttsProvider: "volcark" }, accessToken);
      results.push(audio.size > 0 ? "火山方舟 TTS：连通正常" : "火山方舟 TTS：返回音频为空");
    } catch (error) {
      results.push(`火山方舟 TTS：${error instanceof Error ? error.message : "测试失败"}`);
    } finally {
      setIsTestingNetworkModels(false);
    }

    setSettingsMessage(results.join("；"));
  }

  return (
    <main className="min-h-screen px-6 py-10">
      {isSettingsOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-6 py-8">
          <div className="max-h-full w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-2xl font-bold tracking-tight">模型设置</h2>
                <p className="mt-2 text-sm text-muted-foreground">配置用于例句生成、英中翻译和英语发音的模型服务。</p>
              </div>
              <Button onClick={() => setIsSettingsOpen(false)} type="button" variant="outline">
                关闭
              </Button>
            </div>

            <div className="mt-6 grid gap-5 md:grid-cols-2">
              <div className="space-y-2 md:col-span-2">
                <label className="text-sm font-medium">模型运行模式</label>
                <div className="relative h-14 rounded-full border border-input bg-muted p-1">
                  <div
                    className={`absolute bottom-1 top-1 w-[calc(50%-0.25rem)] rounded-full bg-white shadow-sm transition-transform ${
                      modelSettings.modelMode === "online" ? "translate-x-[calc(100%+0.5rem)]" : "translate-x-0"
                    }`}
                  />
                  <div className="pointer-events-none relative z-10 grid h-full grid-cols-2 items-center text-center text-sm font-semibold">
                    <span className={modelSettings.modelMode === "local" ? "text-primary" : "text-muted-foreground"}>本地模型</span>
                    <span className={modelSettings.modelMode === "online" ? "text-primary" : "text-muted-foreground"}>网络模型</span>
                  </div>
                  <input
                    aria-label="模型运行模式"
                    className="absolute inset-0 z-20 h-full w-full cursor-grab opacity-0 active:cursor-grabbing"
                    max={1}
                    min={0}
                    step={1}
                    type="range"
                    value={modelSettings.modelMode === "online" ? 1 : 0}
                    onChange={(event) => handleModelModeChange(Number(event.target.value) === 1 ? "online" : "local")}
                  />
                </div>
              </div>
              {modelSettings.modelMode === "online" ? (
                <div className="border-t pt-5 md:col-span-2">
                  <h3 className="text-base font-semibold">LLM 模型（DeepSeek）</h3>
                </div>
              ) : null}
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
                  {modelSettings.modelMode === "online" ? "OpenAI Base URL" : "Ollama 调用地址"}
                </label>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="llm-base-url"
                  value={modelSettings.llmBaseUrl}
                  onChange={(event) => handleModelSettingsChange("llmBaseUrl", event.target.value)}
                />
              </div>
              {modelSettings.modelMode === "online" ? (
                <div className="space-y-2 md:col-span-2">
                  <label className="text-sm font-medium" htmlFor="llm-api-key">DeepSeek API Key</label>
                  <input
                    autoComplete="off"
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="llm-api-key"
                    placeholder="稍后填写"
                    type="password"
                    value={modelSettings.llmApiKey}
                    onChange={(event) => handleModelSettingsChange("llmApiKey", event.target.value)}
                  />
                </div>
              ) : null}

              {modelSettings.modelMode === "online" ? (
                <div className="border-t pt-5 md:col-span-2">
                  <h3 className="text-base font-semibold">TTS 模型（火山方舟）</h3>
                </div>
              ) : null}
              {modelSettings.modelMode === "local" ? (
                <div className="space-y-2 md:col-span-2">
                  <label className="text-sm font-medium" htmlFor="tts-api-url">Kokoro API 地址</label>
                  <input
                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                    id="tts-api-url"
                    value={modelSettings.ttsApiUrl}
                    onChange={(event) => handleModelSettingsChange("ttsApiUrl", event.target.value)}
                  />
                </div>
              ) : (
                <>
                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor="volcengine-tts-app-id">APP ID</label>
                    <input
                      autoComplete="off"
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="volcengine-tts-app-id"
                      placeholder="稍后填写"
                      value={modelSettings.volcengineTtsAppId}
                      onChange={(event) => handleModelSettingsChange("volcengineTtsAppId", event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor="volcengine-tts-access-token">Access Token</label>
                    <input
                      autoComplete="off"
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="volcengine-tts-access-token"
                      placeholder="稍后填写"
                      type="password"
                      value={modelSettings.volcengineTtsAccessToken}
                      onChange={(event) => handleModelSettingsChange("volcengineTtsAccessToken", event.target.value)}
                    />
                  </div>
                  <div className="space-y-2 md:col-span-2">
                    <label className="text-sm font-medium" htmlFor="volcengine-tts-secret-key">Secret Key / API Key</label>
                    <input
                      autoComplete="off"
                      className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                      id="volcengine-tts-secret-key"
                      placeholder="稍后填写"
                      type="password"
                      value={modelSettings.volcengineTtsSecretKey}
                      onChange={(event) => handleModelSettingsChange("volcengineTtsSecretKey", event.target.value)}
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
                {modelSettings.modelMode === "local" ? (
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
                {modelSettings.modelMode === "local" ? (
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
              {modelSettings.modelMode === "local" ? (
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
            </div>

            <div className="mt-6 flex flex-wrap justify-end gap-3">
              <Button onClick={() => setModelSettings(defaultModelSettings)} type="button" variant="outline">
                恢复默认
              </Button>
              {modelSettings.modelMode === "online" ? (
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
        <div className="rounded-3xl bg-white p-8 shadow-sm md:p-12">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-primary">MemoSeed</p>
          <h1 className="mt-4 max-w-3xl text-4xl font-bold tracking-tight md:text-6xl">
            英语记忆种子
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
            基于艾宾浩斯记忆曲线、SM-2 和 AI 动态调度，专注中小学阶段英语基础薄弱学生的单词、短语与简单句长期记忆。
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild>
              <Link href={isLoggedIn ? "/learning/import" : "/login"}>开始学习</Link>
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

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {workflowCards.map((card) => {
            const Icon = card.icon;
            return (
              <Card key={card.title}>
                <CardHeader>
                  <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <CardTitle>{card.title}</CardTitle>
                  <CardDescription>{card.minutes}</CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-sm leading-6 text-muted-foreground">{card.description}</p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>
    </main>
  );
}

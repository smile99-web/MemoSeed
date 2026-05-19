"use client";

import { Brain, CalendarClock, GraduationCap, Repeat2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AuthUser, clearAuthSession, getAuthUser, isAuthenticated } from "@/lib/auth";
import { ModelSettings, defaultModelSettings, getModelSettings, saveModelSettings } from "@/lib/model-settings";

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
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);

  useEffect(() => {
    setCurrentUser(isAuthenticated() ? getAuthUser() : null);
    setModelSettings(getModelSettings());
    setHasLoadedSession(true);
  }, []);

  const isLoggedIn = Boolean(currentUser);

  function handleLogout() {
    clearAuthSession();
    setCurrentUser(null);
  }

  function handleModelSettingsChange(field: keyof ModelSettings, value: string) {
    setModelSettings((current) => ({ ...current, [field]: value }));
  }

  function handleSaveModelSettings() {
    saveModelSettings(modelSettings);
    setIsSettingsOpen(false);
  }

  return (
    <main className="min-h-screen px-6 py-10">
      {isSettingsOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-6 py-8">
          <div className="max-h-full w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-2xl font-bold tracking-tight">模型设置</h2>
                <p className="mt-2 text-sm text-muted-foreground">配置用于例句生成、英中翻译和英语发音的本地模型服务。</p>
              </div>
              <Button onClick={() => setIsSettingsOpen(false)} type="button" variant="outline">
                关闭
              </Button>
            </div>

            <div className="mt-6 grid gap-5 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="llm-provider">LLM 提供方</label>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="llm-provider"
                  value={modelSettings.llmProvider}
                  onChange={(event) => handleModelSettingsChange("llmProvider", event.target.value)}
                />
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
              <div className="space-y-2 md:col-span-2">
                <label className="text-sm font-medium" htmlFor="llm-base-url">Ollama 调用地址</label>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="llm-base-url"
                  value={modelSettings.llmBaseUrl}
                  onChange={(event) => handleModelSettingsChange("llmBaseUrl", event.target.value)}
                />
                <p className="text-xs text-muted-foreground">用于生成英语例句，或把英语单词和句子翻译成中文。</p>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="tts-provider">TTS 提供方</label>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="tts-provider"
                  value={modelSettings.ttsProvider}
                  onChange={(event) => handleModelSettingsChange("ttsProvider", event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="tts-api-url">Kokoro API 地址</label>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  id="tts-api-url"
                  value={modelSettings.ttsApiUrl}
                  onChange={(event) => handleModelSettingsChange("ttsApiUrl", event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="tts-english-voice">英文语音音色</label>
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
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="tts-chinese-voice">中文语音音色</label>
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
              </div>
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
                <p className="text-xs text-muted-foreground">TTS 用于英语单词和句子的发音。</p>
              </div>
            </div>

            <div className="mt-6 flex flex-wrap justify-end gap-3">
              <Button onClick={() => setModelSettings(defaultModelSettings)} type="button" variant="outline">
                恢复默认
              </Button>
              <Button onClick={handleSaveModelSettings} type="button">
                保存设置
              </Button>
            </div>
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

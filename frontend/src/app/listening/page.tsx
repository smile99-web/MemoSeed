"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isAuthenticated } from "@/lib/auth";
import {
  ListeningStoryPayload,
  ListeningStorySummary,
  getListeningStories,
  getListeningStory,
} from "@/lib/listening";
import { playCachedAudio, stopAudioPlayback, unlockAudio } from "@/lib/tts";

const TIMER_PRESETS = [5, 10, 15, 20, 30] as const;
/** 连续多少句音频缺失就暂停并提示（正常情况下后端已预热全部音频）。 */
const MAX_CONSECUTIVE_AUDIO_FAILURES = 3;

/**
 * 听力故事页
 * ----------
 * AI 用孩子平时练过的单词生成的双语小故事，逐句播放：先英文、后中文。
 * 一篇播完自动随机切换下一篇；定时器到点自动停止。
 *
 * 播放循环用 sessionRef 做代际失效：停止/切篇/组件卸载时递增，
 * 旧的异步循环在下一次 await 返回后自行退出，避免"幽灵播放"。
 */
export default function ListeningPage() {
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [stories, setStories] = useState<ListeningStorySummary[]>([]);
  const [listError, setListError] = useState<string | null>(null);

  const [currentStory, setCurrentStory] = useState<ListeningStoryPayload | null>(null);
  const [sentenceIndex, setSentenceIndex] = useState(0);
  const [phase, setPhase] = useState<"en1" | "en2" | "zh">("en1");
  const [isPlaying, setIsPlaying] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [timerMinutes, setTimerMinutes] = useState<number>(10);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  // 代际计数器 + 停止标记：递增 session 让旧循环自然退出。
  const sessionRef = useRef(0);
  const stopRef = useRef(true);
  const pausedRef = useRef(false);
  const storiesRef = useRef<ListeningStorySummary[]>([]);
  storiesRef.current = stories;

  useEffect(() => {
    setLoggedIn(isAuthenticated());
  }, []);

  useEffect(() => {
    if (!loggedIn) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const list = await getListeningStories();
        if (!cancelled) {
          setStories(list);
          if (list.length === 0) {
            setListError("还没有听力故事，请先在服务器上运行生成脚本。");
          }
        }
      } catch (error) {
        if (!cancelled) {
          setListError(error instanceof Error ? error.message : "故事列表加载失败");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loggedIn]);

  // 定时器：仅在播放中倒计时，暂停时保持。
  useEffect(() => {
    if (!isPlaying || isPaused || secondsLeft === null) {
      return;
    }
    if (secondsLeft <= 0) {
      handleStop("⏰ 时间到啦，今天听得很棒！");
      return;
    }
    const id = window.setTimeout(() => setSecondsLeft((s) => (s === null ? null : s - 1)), 1000);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPlaying, isPaused, secondsLeft]);

  // 组件卸载时停止播放。
  useEffect(() => {
    return () => {
      stopRef.current = true;
      sessionRef.current += 1;
      stopAudioPlayback();
    };
  }, []);

  function pickRandomStory(excludeId?: string): ListeningStorySummary | null {
    const pool = storiesRef.current.filter((s) => s.id !== excludeId);
    const candidates = pool.length > 0 ? pool : storiesRef.current;
    if (candidates.length === 0) {
      return null;
    }
    return candidates[Math.floor(Math.random() * candidates.length)];
  }

  async function playStoryLoop(story: ListeningStoryPayload, startIndex: number, session: number) {
    let failures = 0;
    for (let i = startIndex; i < story.sentences.length; i += 1) {
      if (sessionRef.current !== session || stopRef.current) {
        return;
      }
      setSentenceIndex(i);
      // 英文读两遍、中文读一遍（家长要求：多听英文，中文只作对照）。
      setPhase("en1");
      try {
        await playCachedAudio(story.sentences[i].en_audio_url);
        failures = 0;
      } catch {
        failures += 1;
      }
      if (sessionRef.current !== session || stopRef.current) {
        return;
      }
      setPhase("en2");
      try {
        await playCachedAudio(story.sentences[i].en_audio_url);
        failures = 0;
      } catch {
        failures += 1;
      }
      if (sessionRef.current !== session || stopRef.current) {
        return;
      }
      setPhase("zh");
      try {
        await playCachedAudio(story.sentences[i].zh_audio_url);
        failures = 0;
      } catch {
        failures += 1;
      }
      if (failures >= MAX_CONSECUTIVE_AUDIO_FAILURES) {
        setNotice("多句音频缺失，已暂停。请联系家长在服务器上预热音频后重试。");
        setIsPlaying(false);
        setIsPaused(false);
        stopRef.current = true;
        return;
      }
    }
    // 一篇播完 → 随机下一篇（"继续随机播放下一篇英文故事"）。
    if (sessionRef.current !== session || stopRef.current) {
      return;
    }
    const next = pickRandomStory(story.id);
    if (!next) {
      setIsPlaying(false);
      stopRef.current = true;
      return;
    }
    try {
      const payload = await getListeningStory(next.id);
      if (sessionRef.current !== session || stopRef.current) {
        return;
      }
      setCurrentStory(payload);
      setSentenceIndex(0);
      setNotice(`📖 下一篇：${payload.title}`);
      await playStoryLoop(payload, 0, session);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "下一篇故事加载失败");
      setIsPlaying(false);
      stopRef.current = true;
    }
  }

  async function startPlayback(storyId?: string, startIndex = 0) {
    unlockAudio();
    stopAudioPlayback();
    setNotice(null);
    const summary = storyId
      ? storiesRef.current.find((s) => s.id === storyId) ?? null
      : pickRandomStory();
    if (!summary) {
      setNotice("没有可播放的故事。");
      return;
    }
    const session = sessionRef.current + 1;
    sessionRef.current = session;
    stopRef.current = false;
    pausedRef.current = false;
    setIsPaused(false);
    setIsPlaying(true);
    setSecondsLeft(timerMinutes * 60);
    try {
      const payload = await getListeningStory(summary.id);
      if (sessionRef.current !== session || stopRef.current) {
        return;
      }
      setCurrentStory(payload);
      setSentenceIndex(startIndex);
      setPhase("en1");
      await playStoryLoop(payload, startIndex, session);
    } catch (error) {
      if (sessionRef.current === session) {
        setNotice(error instanceof Error ? error.message : "故事加载失败");
        setIsPlaying(false);
        stopRef.current = true;
      }
    }
  }

  function handlePause() {
    pausedRef.current = true;
    setIsPaused(true);
    stopAudioPlayback();
    // 不递增 session：playStoryLoop 在 await 返回后检查 stopRef 才会退出——
    // 这里直接递增，让当前循环干净退出，恢复时从当前句重播。
    sessionRef.current += 1;
    stopRef.current = true;
  }

  function handleResume() {
    if (!currentStory) {
      return;
    }
    pausedRef.current = false;
    setIsPaused(false);
    unlockAudio();
    stopAudioPlayback();
    const session = sessionRef.current + 1;
    sessionRef.current = session;
    stopRef.current = false;
    setIsPlaying(true);
    void playStoryLoop(currentStory, sentenceIndex, session);
  }

  function handleStop(message?: string) {
    stopRef.current = true;
    sessionRef.current += 1;
    stopAudioPlayback();
    setIsPlaying(false);
    setIsPaused(false);
    setSecondsLeft(null);
    if (message) {
      setNotice(message);
    }
  }

  function formatCountdown(totalSeconds: number): string {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  if (loggedIn === false) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>🎧 听力故事</CardTitle>
            <CardDescription>请先登录后再听故事。</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <Link href="/login">去登录</Link>
            </Button>
          </CardContent>
        </Card>
      </main>
    );
  }

  const currentSentence = currentStory?.sentences[sentenceIndex] ?? null;

  return (
    <main className="min-h-screen px-6 py-10 ipad:px-8 ipad:py-14">
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-3 glass-card px-4 py-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight ipad:text-3xl">🎧 听力故事</h1>
            <p className="text-sm text-muted-foreground">用你练过的单词编成的英文小故事和日常对话，英文读两遍、中文读一遍。</p>
          </div>
          <Button asChild variant="outline">
            <Link href="/">返回首页</Link>
          </Button>
        </div>

        <div className="flex flex-col gap-6">
          {/* 播放器主区 */}
          <Card className="min-h-[420px]">
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-xl ipad:text-2xl">
                    {currentStory ? currentStory.title : "准备好了吗？"}
                  </CardTitle>
                  <CardDescription>
                    {currentStory
                      ? `第 ${sentenceIndex + 1} / ${currentStory.sentences.length} 句`
                      : "点「开始播放」，随机听一篇小故事。"}
                  </CardDescription>
                </div>
                {secondsLeft !== null ? (
                  <div className="rounded-full border border-cyan-200 bg-cyan-50 px-4 py-1.5 text-lg font-bold tabular-nums text-cyan-700">
                    ⏱ {formatCountdown(secondsLeft)}
                  </div>
                ) : null}
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-6">
              {currentSentence ? (
                <div className="rounded-2xl border border-slate-200 bg-white/70 p-6 text-center">
                  {currentSentence.speaker ? (
                    <span
                      className={`mb-3 inline-flex h-9 w-9 items-center justify-center rounded-full text-base font-black text-white shadow-sm ${
                        currentSentence.speaker === "A" ? "bg-cyan-500" : "bg-violet-500"
                      }`}
                    >
                      {currentSentence.speaker}
                    </span>
                  ) : null}
                  <p
                    className={`text-2xl font-bold leading-relaxed transition-colors ipad:text-3xl ${
                      phase !== "zh" && isPlaying && !isPaused ? "text-cyan-600" : "text-slate-800"
                    }`}
                  >
                    {currentSentence.en}
                  </p>
                  <p
                    className={`mt-4 text-lg transition-colors ipad:text-xl ${
                      phase === "zh" && isPlaying && !isPaused ? "font-bold text-violet-600" : "text-muted-foreground"
                    }`}
                  >
                    {currentSentence.zh}
                  </p>
                  {currentStory ? (
                    <div className="mt-6 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-violet-400 transition-all"
                        style={{ width: `${((sentenceIndex + 1) / currentStory.sentences.length) * 100}%` }}
                      />
                    </div>
                  ) : null}
                  {isPlaying && !isPaused ? (
                    <p className="mt-3 text-sm text-muted-foreground">
                      {phase === "zh"
                        ? `🔊 ${currentSentence.speaker ? `${currentSentence.speaker} 正在读中文` : "正在读中文"}…`
                        : phase === "en1"
                          ? `🔊 ${currentSentence.speaker ? `${currentSentence.speaker} 正在读英文` : "正在读英文"}（第 1 遍）…`
                          : `🔊 ${currentSentence.speaker ? `${currentSentence.speaker} 正在读英文` : "正在读英文"}（第 2 遍）…`}
                    </p>
                  ) : null}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-300 p-10 text-center text-muted-foreground">
                  {listError ?? "故事加载中…"}
                </div>
              )}

              {notice ? <p className="text-center text-sm text-amber-600">{notice}</p> : null}

              {/* 控制区 */}
              <div className="flex flex-wrap items-center justify-center gap-3">
                {!isPlaying ? (
                  <Button className="ipad:px-6 ipad:py-6 ipad:text-lg" onClick={() => void startPlayback()} size="lg">
                    ▶️ 开始播放
                  </Button>
                ) : isPaused ? (
                  <Button className="ipad:px-6 ipad:py-6 ipad:text-lg" onClick={handleResume} size="lg">
                    ▶️ 继续
                  </Button>
                ) : (
                  <Button className="ipad:px-6 ipad:py-6 ipad:text-lg" onClick={handlePause} size="lg" variant="secondary">
                    ⏸ 暂停
                  </Button>
                )}
                {isPlaying ? (
                  <>
                    <Button onClick={() => void startPlayback()} variant="outline">
                      ⏭ 换一篇
                    </Button>
                    <Button onClick={() => handleStop()} variant="outline">
                      ⏹ 停止
                    </Button>
                  </>
                ) : null}
              </div>

              {/* 定时设置 */}
              <div className="flex flex-wrap items-center justify-center gap-2">
                <span className="text-sm text-muted-foreground">定时停止：</span>
                {TIMER_PRESETS.map((minutes) => (
                  <button
                    className={`rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
                      timerMinutes === minutes
                        ? "bg-cyan-600 text-white"
                        : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                    }`}
                    key={minutes}
                    onClick={() => {
                      setTimerMinutes(minutes);
                      if (isPlaying) {
                        setSecondsLeft(minutes * 60);
                      }
                    }}
                    type="button"
                  >
                    {minutes} 分钟
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* 目录：对话与故事分成两个框，显示在播放器正下方 */}
          <div className="grid gap-6 lg:grid-cols-2">
            {[
              {
                key: "dialogue",
                title: "💬 对话目录",
                description: "A/B 一问一答，两个人两种音色",
                items: stories.filter((s) => s.kind === "dialogue"),
              },
              {
                key: "story",
                title: "📖 故事目录",
                description: "用练过的单词编成的双语小故事",
                items: stories.filter((s) => s.kind !== "dialogue"),
              },
            ].map((group) => (
              <Card key={group.key}>
                <CardHeader>
                  <CardTitle className="text-lg">{group.title}</CardTitle>
                  <CardDescription>{group.description} · 点一篇直接播放</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  {group.items.map((story) => (
                    <button
                      className={`rounded-xl border px-4 py-3 text-left transition-colors ${
                        currentStory?.id === story.id
                          ? "border-cyan-400 bg-cyan-50"
                          : "border-slate-200 bg-white/60 hover:border-cyan-200 hover:bg-cyan-50/50"
                      }`}
                      key={story.id}
                      onClick={() => void startPlayback(story.id)}
                      type="button"
                    >
                      <p className="font-medium leading-snug">
                        {story.kind === "dialogue" ? "💬 " : "📖 "}
                        {story.title}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {story.theme} · {story.sentence_count} {story.kind === "dialogue" ? "轮" : "句"}
                      </p>
                    </button>
                  ))}
                  {group.items.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{stories.length === 0 && !listError ? "加载中…" : "暂无内容"}</p>
                  ) : null}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </div>
    </main>
  );
}

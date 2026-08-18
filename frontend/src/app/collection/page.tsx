"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { fetchWithAuth, parseApiError } from "@/lib/api";
import { getApiBaseUrl } from "@/lib/api-base-url";
import { getAccessToken } from "@/lib/auth";

/**
 * 四期改造(2026-08-18): 单词收藏册 + 课程进度地图
 * ------------------------------------------------
 * 收藏的每一个词都是孩子亲手"点亮"的:mastered / near_mastered / 五维全毕业。
 * 点击单词卡播放发音(浏览器 TTS,免流量)。课程地图展示每门课的点亮进度,
 * 让孩子对"我积累了多少"有直接的体感——这是坚持的最强动机。
 *
 * 数据来源:GET /api/v1/memory/word-collection
 */

interface DimensionProgress {
  days: number;
  graduated: boolean;
}

interface CollectedWord {
  word: string;
  chinese: string;
  phonetic: string;
  status: string;
  memory_strength: number;
  dimensions: Record<string, DimensionProgress> & { last_failed?: string | null };
}

interface CourseMapEntry {
  course_id: string;
  course_name: string;
  total_words: number;
  collected_count: number;
  completed: boolean;
}

interface WordCollectionResponse {
  total_collected: number;
  in_flight_count: number;
  mastered_words: CollectedWord[];
  courses: CourseMapEntry[];
}

const DIM_LABELS: Record<string, string> = {
  listen: "听",
  meaning: "义",
  speak: "读",
  spell: "写",
  use: "用",
};

function speakWord(word: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) {
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(word);
  utterance.lang = "en-US";
  utterance.rate = 0.85;
  window.speechSynthesis.speak(utterance);
}

export default function WordCollectionPage() {
  const [data, setData] = useState<WordCollectionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const accessToken = getAccessToken();
      if (!accessToken) {
        setError("请先登录后再查看收藏册");
        setLoading(false);
        return;
      }
      try {
        const response = await fetchWithAuth(
          `${getApiBaseUrl()}/memory/word-collection`,
          { cache: "no-store" },
          accessToken,
        );
        if (!response.ok) {
          throw new Error(await parseApiError(response));
        }
        const payload = (await response.json()) as WordCollectionResponse;
        if (!cancelled) {
          setData(payload);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载收藏册失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredWords = (data?.mastered_words ?? []).filter(
    (entry) =>
      !filter ||
      entry.word.includes(filter.trim().toLowerCase()) ||
      entry.chinese.includes(filter.trim()),
  );

  const playWord = useCallback((word: string) => {
    speakWord(word);
  }, []);

  return (
    <main className="mx-auto flex min-h-[100dvh] max-w-6xl flex-col gap-6 p-4 ipad:p-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link className="text-sm font-medium text-primary hover:underline ipad:text-base" href="/learning">
            返回学习
          </Link>
          <h1 className="mt-2 text-2xl font-bold ipad:text-3xl">
            <span className="text-gradient">📖 单词收藏册</span>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground ipad:text-base">
            每一个点亮的词,都是你亲手学会的。点一点单词可以听发音哦!
          </p>
        </div>
        {data ? (
          <div className="flex gap-3">
            <div className="rounded-2xl border border-amber-200/70 bg-amber-50/70 px-5 py-3 text-center shadow-soft">
              <p className="text-2xl font-bold text-amber-600 ipad:text-3xl">{data.total_collected}</p>
              <p className="text-xs text-amber-700 ipad:text-sm">已收藏</p>
            </div>
            <div className="rounded-2xl border border-sky-200/70 bg-sky-50/70 px-5 py-3 text-center shadow-soft">
              <p className="text-2xl font-bold text-sky-600 ipad:text-3xl">{data.in_flight_count}</p>
              <p className="text-xs text-sky-700 ipad:text-sm">学习中</p>
            </div>
          </div>
        ) : null}
      </header>

      {error ? (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      ) : null}

      {loading ? <p className="text-muted-foreground">正在打开收藏册…</p> : null}

      {data && data.courses.length > 0 ? (
        <section className="space-y-3">
          <h2 className="text-xl font-semibold tracking-tight ipad:text-2xl">🗺️ 课程进度地图</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {data.courses.map((course) => {
              const pct = course.total_words > 0 ? Math.round((course.collected_count / course.total_words) * 100) : 0;
              return (
                <div
                  key={course.course_id}
                  className="rounded-2xl border border-white/70 bg-white/70 px-5 py-4 shadow-soft backdrop-blur-xl"
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-base font-bold text-slate-700 ipad:text-lg">
                      {course.completed ? "🏆" : "📗"} {course.course_name}
                    </p>
                    <span className="text-sm font-semibold text-slate-500">
                      {course.collected_count}/{course.total_words}
                    </span>
                  </div>
                  <div className="mt-3 h-3 overflow-hidden rounded-full bg-slate-200/80">
                    <div
                      className={`h-full rounded-full transition-all ${course.completed ? "bg-amber-400" : "bg-emerald-400"}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{course.completed ? "全部点亮,太厉害了!" : `已点亮 ${pct}%`}</p>
                </div>
              );
            })}
          </div>
        </section>
      ) : null}

      {data ? (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-xl font-semibold tracking-tight ipad:text-2xl">⭐ 我的收藏({filteredWords.length})</h2>
            <input
              className="w-48 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm outline-none focus:border-cyan-400 ipad:text-base"
              placeholder="搜一搜单词…"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
          {filteredWords.length === 0 ? (
            <p className="rounded-xl border border-slate-200 bg-white/60 px-4 py-6 text-center text-sm text-slate-500">
              {data.total_collected === 0
                ? "收藏册还是空的。去学今天的五阶段任务,点亮你的第一个单词吧!"
                : "没有找到这个词,换个关键词试试。"}
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
              {filteredWords.map((entry) => (
                <button
                  key={entry.word}
                  type="button"
                  onClick={() => playWord(entry.word)}
                  className="hover-lift rounded-2xl border border-white/70 bg-white/70 px-4 py-3 text-left shadow-soft backdrop-blur-xl transition-colors hover:border-cyan-300"
                  title="点击听发音"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-lg font-bold text-slate-800">{entry.word}</p>
                    <span className="text-base">🔊</span>
                  </div>
                  {entry.phonetic ? <p className="mt-0.5 text-xs text-slate-400">{entry.phonetic}</p> : null}
                  <p className="mt-0.5 truncate text-sm text-slate-500">{entry.chinese || "—"}</p>
                  <div className="mt-2 flex gap-1">
                    {Object.entries(DIM_LABELS).map(([dim, label]) => {
                      const graduated = entry.dimensions?.[dim]?.graduated;
                      return (
                        <span
                          key={dim}
                          className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold ${
                            graduated ? "bg-emerald-100 text-emerald-600" : "bg-slate-100 text-slate-300"
                          }`}
                        >
                          {label}
                        </span>
                      );
                    })}
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>
      ) : null}

      <div className="pb-8">
        <Button asChild variant="secondary">
          <Link href="/learning">继续学习,点亮更多单词</Link>
        </Button>
      </div>
    </main>
  );
}

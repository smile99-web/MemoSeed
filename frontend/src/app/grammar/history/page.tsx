"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { GrammarHistoryResponse, GrammarLevelStat, GrammarSessionSummary, getGrammarHistory } from "@/lib/grammar";

/**
 * 语法练习历史页
 * ----------------
 * 显示:
 *  - 累计数据(总练习次数、总答题数、整体正确率)
 *  - 每个 Level 的细分统计(次数、正确率、平均答题时间)
 *  - 最近 20 场练习的明细
 *
 * 数据来源:GET /api/v1/grammar/history
 * 时间显示:相对时间(几分钟前/几小时前/几天前) + 绝对时间
 */
export default function GrammarHistoryPage() {
  const [history, setHistory] = useState<GrammarHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await getGrammarHistory();
        if (!cancelled) {
          setHistory(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载历史失败");
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

  return (
    <main className="mx-auto flex min-h-[100dvh] max-w-5xl flex-col gap-6 p-4 ipad:p-8">
      <header className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-primary">Grammar</p>
          <h1 className="mt-1 text-2xl font-bold ipad:text-3xl">练习历史</h1>
          <p className="mt-1 text-sm text-muted-foreground ipad:text-base">
            看看最近每场练习的准确率和答题时间。
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button asChild variant="outline">
            <Link href="/grammar">继续练习</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/">返回首页</Link>
          </Button>
        </div>
      </header>

      {loading ? (
        <Card>
          <CardContent className="flex items-center justify-center gap-3 py-12">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">加载中...</p>
          </CardContent>
        </Card>
      ) : null}

      {error ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-red-600">加载失败</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {history && !loading && !error ? (
        <>
          <OverallStats
            totalSessions={history.total_sessions}
            totalQuestions={history.total_questions}
            overallAccuracy={history.overall_accuracy}
          />
          <PerLevelStats stats={history.per_level} />
          <RecentSessions sessions={history.recent_sessions} />
        </>
      ) : null}

      {history && history.total_sessions === 0 && !loading ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-sm text-muted-foreground ipad:text-base">
              还没有练习记录。点击下面的按钮开始第一组题目!
            </p>
            <Button asChild className="mt-4">
              <Link href="/grammar">开始练习</Link>
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </main>
  );
}

function OverallStats({
  totalSessions,
  totalQuestions,
  overallAccuracy,
}: {
  totalSessions: number;
  totalQuestions: number;
  overallAccuracy: number;
}) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <StatCard label="完成练习" value={`${totalSessions}`} suffix="场" />
      <StatCard label="累计答题" value={`${totalQuestions}`} suffix="题" />
      <StatCard
        label="整体正确率"
        value={`${Math.round(overallAccuracy * 100)}`}
        suffix="%"
        accent={overallAccuracy >= 0.7 ? "emerald" : overallAccuracy >= 0.5 ? "amber" : "slate"}
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  suffix,
  accent,
}: {
  label: string;
  value: string;
  suffix: string;
  accent?: "emerald" | "amber" | "slate";
}) {
  const accentCls =
    accent === "emerald"
      ? "text-emerald-600"
      : accent === "amber"
      ? "text-amber-600"
      : "text-slate-700";
  return (
    <Card>
      <CardContent className="py-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className={`mt-1 text-3xl font-bold ipad:text-4xl ${accentCls}`}>
          {value}
          <span className="ml-1 text-base font-normal text-muted-foreground">{suffix}</span>
        </p>
      </CardContent>
    </Card>
  );
}

function PerLevelStats({ stats }: { stats: GrammarLevelStat[] }) {
  if (stats.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>按难度统计</CardTitle>
        <CardDescription>每个 Level 的累计表现</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm ipad:text-base">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="py-2 pr-3">Level</th>
                <th className="py-2 pr-3">练习场数</th>
                <th className="py-2 pr-3">总答题数</th>
                <th className="py-2 pr-3">正确率</th>
                <th className="py-2 pr-3">平均答题时长</th>
              </tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={s.level} className="border-b last:border-0">
                  <td className="py-2 pr-3 font-semibold">L{s.level}</td>
                  <td className="py-2 pr-3">{s.total_sessions}</td>
                  <td className="py-2 pr-3">{s.total_questions}</td>
                  <td className="py-2 pr-3">
                    <span
                      className={
                        s.accuracy >= 0.8
                          ? "font-semibold text-emerald-600"
                          : s.accuracy >= 0.5
                          ? "text-amber-600"
                          : "text-slate-500"
                      }
                    >
                      {Math.round(s.accuracy * 100)}%
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">
                    {(s.avg_time_per_question_ms / 1000).toFixed(1)}s
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function RecentSessions({ sessions }: { sessions: GrammarSessionSummary[] }) {
  if (sessions.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>最近练习</CardTitle>
        <CardDescription>最多显示 20 场最近完成的练习</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm ipad:text-base">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="py-2 pr-3">完成时间</th>
                <th className="py-2 pr-3">Level</th>
                <th className="py-2 pr-3">得分</th>
                <th className="py-2 pr-3">题型</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id} className="border-b last:border-0">
                  <td className="py-2 pr-3 text-muted-foreground">
                    {s.completed_at ? formatRelativeTime(s.completed_at) : "进行中"}
                  </td>
                  <td className="py-2 pr-3 font-semibold">L{s.level}</td>
                  <td className="py-2 pr-3">
                    <span
                      className={
                        s.accuracy >= 0.8
                          ? "font-semibold text-emerald-600"
                          : s.accuracy >= 0.5
                          ? "text-amber-600"
                          : "text-slate-500"
                      }
                    >
                      {s.correct_count} / {s.total_questions}
                    </span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      ({Math.round(s.accuracy * 100)}%)
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted-foreground">
                    {s.choice_questions}选 {s.fill_in_questions}填
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function formatRelativeTime(isoString: string): string {
  const completedAt = new Date(isoString).getTime();
  if (Number.isNaN(completedAt)) return isoString;
  const now = Date.now();
  const diffMs = now - completedAt;
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return new Date(isoString).toLocaleDateString("zh-CN");
}

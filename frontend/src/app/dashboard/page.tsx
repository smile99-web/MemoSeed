"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiRequest } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import { fitFsrsParameters, getMemoryDashboard, MemoryDashboard, WordMasterySummary } from "@/lib/memory";

const statusLabels: Record<WordMasterySummary["status"], string> = {
  mastered: "已掌握",
  learning: "学习中",
  weak: "未掌握",
};

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "暂无";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours === 0 && minutes === 0) {
    return `${seconds} 秒`;
  }
  if (hours === 0) {
    return `${minutes} 分钟`;
  }
  return `${hours} 小时 ${minutes} 分钟`;
}

function StatCard({ label, value, hint }: { label: string; value: string | number; hint: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription className="ipad:text-base">{label}</CardDescription>
        <CardTitle className="text-3xl ipad:text-4xl">{value}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground ipad:text-base">{hint}</p>
      </CardContent>
    </Card>
  );
}

function WordTable({ title, words }: { title: string; words: WordMasterySummary[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="ipad:text-xl">{title}</CardTitle>
        <CardDescription className="ipad:text-base">显示已进入复习记录的单词，按记忆强度、错误次数和遗忘风险排序。</CardDescription>
      </CardHeader>
      <CardContent>
        {words.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无数据。</p>
        ) : (
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted">
                <tr>
                  <th className="px-3 py-2">单词</th>
                  <th className="px-3 py-2">状态</th>
                  <th className="px-3 py-2">优先级</th>
                  <th className="px-3 py-2">强度</th>
                  <th className="px-3 py-2">风险</th>
                  <th className="px-3 py-2">复习</th>
                  <th className="px-3 py-2">错次</th>
                  <th className="px-3 py-2">下次复习</th>
                </tr>
              </thead>
              <tbody>
                {words.map((word) => (
                  <tr className="border-t" key={`${title}-${word.word}`}>
                    <td className="px-3 py-2 font-medium">{word.word}</td>
                    <td className="px-3 py-2">{statusLabels[word.status]}</td>
                    <td className="px-3 py-2">{formatPercent(word.priority_score)}</td>
                    <td className="px-3 py-2">{formatPercent(word.memory_strength)}</td>
                    <td className="px-3 py-2">{formatPercent(word.forget_risk)}</td>
                    <td className="px-3 py-2">{word.review_count}</td>
                    <td className="px-3 py-2">{word.mistake_count}</td>
                    <td className="px-3 py-2">{formatDateTime(word.next_review_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<MemoryDashboard | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isFittingFsrs, setIsFittingFsrs] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [fsrsFitMessage, setFsrsFitMessage] = useState<string | null>(null);
  const importInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    async function loadDashboard() {
      const accessToken = getAccessToken();
      if (!accessToken) {
        setErrorMessage("请先登录后再查看数据看板");
        setIsLoading(false);
        return;
      }

      try {
        setDashboard(await getMemoryDashboard(accessToken));
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : "读取数据看板失败");
      } finally {
        setIsLoading(false);
      }
    }

    void loadDashboard();
  }, []);

  async function exportUserData() {
    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再导出数据");
      return;
    }
    setIsExporting(true);
    try {
      const exportedData = await apiRequest<Record<string, unknown>>("/reports/export", { accessToken });
      const blob = new Blob([JSON.stringify(exportedData, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `memoseed-export-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "导出数据失败");
    } finally {
      setIsExporting(false);
    }
  }

  async function importUserData(file: File | null) {
    if (!file) {
      return;
    }
    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再导入数据");
      return;
    }
    setIsImporting(true);
    try {
      const payload = JSON.parse(await file.text()) as Record<string, unknown>;
      await apiRequest<{ message: string; imported: Record<string, number> }, Record<string, unknown>>("/reports/import", {
        method: "POST",
        accessToken,
        body: payload,
      });
      setDashboard(await getMemoryDashboard(accessToken));
      setErrorMessage(null);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "导入数据失败");
    } finally {
      setIsImporting(false);
      if (importInputRef.current) {
        importInputRef.current.value = "";
      }
    }
  }

  async function fitFsrsFromHistory() {
    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再拟合 FSRS 参数");
      return;
    }

    setIsFittingFsrs(true);
    setFsrsFitMessage(null);
    try {
      const result = await fitFsrsParameters(accessToken);
      setDashboard(await getMemoryDashboard(accessToken));
      setErrorMessage(null);
      setFsrsFitMessage(`已使用 ${result.training_review_count} 条答题记录和 ${result.training_pair_count} 组连续复习样本完成 FSRS 参数拟合。`);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "FSRS 参数拟合失败");
    } finally {
      setIsFittingFsrs(false);
    }
  }

  return (
    <main className="min-h-screen px-6 py-10 ipad:px-8 ipad:py-14">
      <section className="mx-auto max-w-6xl space-y-6 ipad:space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <Link className="text-sm font-medium text-primary hover:underline ipad:text-base" href="/">
              返回首页
            </Link>
            <h1 className="mt-4 text-3xl font-bold tracking-tight ipad:text-4xl">学习数据看板</h1>
            <p className="mt-2 text-muted-foreground ipad:text-lg">基于 FSRS 复习记录、错词日志和记忆强度生成。</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <input
              ref={importInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(event) => {
                void importUserData(event.target.files?.[0] ?? null);
              }}
            />
            <Button type="button" variant="outline" className="ipad:text-lg ipad:px-6 ipad:py-3" onClick={() => importInputRef.current?.click()} disabled={isImporting}>
              {isImporting ? "正在导入" : "导入数据"}
            </Button>
            <Button type="button" variant="outline" className="ipad:text-lg ipad:px-6 ipad:py-3" onClick={exportUserData} disabled={isExporting}>
              {isExporting ? "正在导出" : "导出数据"}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="ipad:text-lg ipad:px-6 ipad:py-3"
              onClick={fitFsrsFromHistory}
              disabled={isFittingFsrs || !dashboard || dashboard.total_reviews < dashboard.fsrs_min_training_reviews}
              title={dashboard && dashboard.total_reviews < dashboard.fsrs_min_training_reviews ? `至少需要 ${dashboard.fsrs_min_training_reviews} 条历史答题记录` : undefined}
            >
              {isFittingFsrs ? "正在拟合" : "拟合 FSRS 参数"}
            </Button>
            <Button asChild variant="secondary" className="ipad:text-lg ipad:px-6 ipad:py-3">
              <Link href="/learning">继续学习</Link>
            </Button>
          </div>
        </div>

        {isLoading ? <p className="text-sm text-muted-foreground ipad:text-base">正在加载数据...</p> : null}
        {!isLoading && errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 ipad:px-6 ipad:py-4 ipad:text-base">{errorMessage}</p> : null}
        {fsrsFitMessage ? <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 ipad:px-6 ipad:py-4 ipad:text-base">{fsrsFitMessage}</p> : null}

        {dashboard ? (
          <>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <StatCard label="已掌握单词" value={dashboard.mastered_words} hint={`共 ${dashboard.total_words} 个单词，已掌握 ${dashboard.mastered_words} 个`} />
              <StatCard label="未掌握单词" value={dashboard.weak_words} hint={`${dashboard.learning_words} 个仍在学习中`} />
              <StatCard label="复习准确率" value={formatPercent(dashboard.accuracy_rate)} hint={`${dashboard.correct_reviews} / ${dashboard.total_reviews} 次复习正确`} />
              <StatCard label="下次复习" value={formatDateTime(dashboard.next_review_at)} hint={`${dashboard.due_now_count} 项已到期，${dashboard.overdue_count} 项超时`} />
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <StatCard label="平均记忆强度" value={formatPercent(dashboard.average_memory_strength)} hint="越高说明回忆越稳定" />
              <StatCard label="平均遗忘风险" value={formatPercent(dashboard.average_forget_risk)} hint="越高越需要尽快复习" />
              <StatCard label="平均复习间隔" value={`${dashboard.average_interval_days} 天`} hint="由 FSRS 稳定度和英语难度修正共同决定" />
            </div>

            <Card>
              <CardHeader>
                <CardTitle>FSRS 参数</CardTitle>
                <CardDescription>
                  {dashboard.fsrs_parameters_source === "user_fitted"
                    ? `当前使用用户历史数据拟合参数，训练记录 ${dashboard.fsrs_training_review_count} 条，连续复习样本 ${dashboard.fsrs_training_pair_count} 组。`
                    : `当前使用内置 FSRS 权重；历史答题记录达到 ${dashboard.fsrs_min_training_reviews} 条后可拟合个人参数。`}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center justify-between gap-4">
                <div className="space-y-1 text-sm text-muted-foreground">
                  <p>当前模式：{dashboard.fsrs_parameters_source === "user_fitted" ? "个人拟合参数" : "内置权重"}</p>
                  <p>历史答题记录：{dashboard.total_reviews} / {dashboard.fsrs_min_training_reviews}</p>
                  <p>上次拟合：{formatDateTime(dashboard.fsrs_fitted_at)}</p>
                </div>
                <Button
                  type="button"
                  onClick={fitFsrsFromHistory}
                  disabled={isFittingFsrs || dashboard.total_reviews < dashboard.fsrs_min_training_reviews}
                >
                  {isFittingFsrs ? "正在拟合" : dashboard.fsrs_parameters_source === "user_fitted" ? "重新拟合" : "拟合 FSRS 参数"}
                </Button>
              </CardContent>
            </Card>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <StatCard label="今日学习时长" value={formatDuration(dashboard.study_time.today_seconds)} hint="20 秒无键盘输入会自动暂停计时" />
              <StatCard label="本周学习时长" value={formatDuration(dashboard.study_time.week_seconds)} hint="按自然周统计活跃学习时间" />
              <StatCard label="本月学习时长" value={formatDuration(dashboard.study_time.month_seconds)} hint="按自然月统计活跃学习时间" />
              <StatCard label="今年学习时长" value={formatDuration(dashboard.study_time.year_seconds)} hint={`累计 ${formatDuration(dashboard.study_time.total_seconds)}`} />
            </div>

            <Card>
              <CardHeader>
                <CardTitle>复习时间分布</CardTitle>
                <CardDescription>按短期错词强化和长期 FSRS 复习曲线聚合。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {dashboard.review_buckets.map((bucket) => (
                  <div className="grid grid-cols-[6rem_1fr_3rem] items-center gap-3" key={bucket.label}>
                    <span className="text-sm text-muted-foreground">{bucket.label}</span>
                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${dashboard.total_items > 0 ? Math.max((bucket.count / dashboard.total_items) * 100, bucket.count > 0 ? 4 : 0) : 0}%` }}
                      />
                    </div>
                    <span className="text-right text-sm font-medium">{bucket.count}</span>
                  </div>
                ))}
              </CardContent>
            </Card>

            <div className="grid gap-4 xl:grid-cols-2">
              <WordTable title="最需要复习的单词" words={dashboard.weakest_words} />
              <WordTable title="掌握最稳定的单词" words={dashboard.strongest_words} />
            </div>
          </>
        ) : null}
      </section>
    </main>
  );
}

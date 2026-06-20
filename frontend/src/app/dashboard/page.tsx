"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { apiRequest, getFreshAccessToken } from "@/lib/api";
import { PointsHeatmapCompact } from "./replay/points-heatmap";
import { getAccessToken } from "@/lib/auth";
import { DayDetail, Heatmap, HeatmapDay, getDayDetail, getHeatmap } from "@/lib/learning-replay";
import { getModelSettings, savePersistedModelSettings } from "@/lib/model-settings";
import {
  DailyReport,
  ErrorBreakdown,
  fitFsrsParameters,
  generateDailyReport,
  getDailyReport,
  getErrorBreakdown,
  getMemoryDashboard,
  getMemoryDashboardWithCourse,
  getRetentionCurve,
  getPointsSummary,
  getReviewForecast,
  getTodayProgress,
  PointsSummary,
  ReviewForecast,
  TodayProgress,
  getStudyStreak,
  getTodayPlan,
  getWordHistory,
  MemoryDashboard,
  RetentionCurve,
  StudyStreak,
  TodayPlan,
  WordHistoryResponse,
  WordMasterySummary,
} from "@/lib/memory";

const statusLabels: Record<WordMasterySummary["status"], string> = {
  difficult: "困难词",
  teaching: "教学中",
  consolidating: "巩固中",
  near_mastered: "接近掌握",
  mastered: "已掌握",
};

const replayDayLabels = ["", "Mon", "", "Wed", "", "Fri", ""];
const replayMonthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatPercent(value: number): string {
  // Guard against NaN/Infinity: backend can return NaN if a stat is
  // computed from an empty set (e.g. average_forget_risk over zero
  // memory_states). `Math.round(NaN * 100) === NaN` would otherwise
  // render as the literal "NaN%" in the dashboard.
  if (!Number.isFinite(value)) {
    return "0%";
  }
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

function getDaysSince(value: string | null): number | null {
  if (!value) {
    return null;
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return null;
  }
  return Math.floor((Date.now() - timestamp) / 86400000);
}

function getFsrsFitRecommendation(dashboard: MemoryDashboard): { title: string; detail: string; tone: "muted" | "ready" | "wait" } {
  if (dashboard.total_reviews < dashboard.fsrs_min_training_reviews) {
    return {
      title: "暂时不建议拟合",
      detail: `还差 ${dashboard.fsrs_min_training_reviews - dashboard.total_reviews} 条历史答题记录，数据太少时拟合容易不稳定。`,
      tone: "muted",
    };
  }

  if (dashboard.fsrs_parameters_source !== "user_fitted" || !dashboard.fsrs_fitted_at) {
    return {
      title: "建议现在拟合",
      detail: `历史答题记录已达到 ${dashboard.total_reviews} 条，可以从内置权重切换为个人拟合参数。`,
      tone: "ready",
    };
  }

  const newReviewCount = Math.max(dashboard.total_reviews - dashboard.fsrs_training_review_count, 0);
  const daysSinceFit = getDaysSince(dashboard.fsrs_fitted_at);
  if (newReviewCount >= 1500) {
    return {
      title: "建议重新拟合",
      detail: `上次拟合后新增 ${newReviewCount} 条答题记录，已经有足够新数据更新参数。`,
      tone: "ready",
    };
  }
  if (daysSinceFit !== null && daysSinceFit >= 30 && newReviewCount >= 500) {
    return {
      title: "可以考虑重新拟合",
      detail: `距离上次拟合 ${daysSinceFit} 天，新增 ${newReviewCount} 条记录，可以用近期数据校准一次。`,
      tone: "ready",
    };
  }

  return {
    title: "暂不建议重复拟合",
    detail: `上次拟合后新增 ${newReviewCount} 条记录${daysSinceFit !== null ? `，已过 ${daysSinceFit} 天` : ""}；建议新增约 1500 条或间隔约 1 个月后再拟合。`,
    tone: "wait",
  };
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

function buildReplayGrid(heatmap: Heatmap | null): {
  weeks: Array<Array<HeatmapDay | null>>;
  monthLabels: Array<{ week: number; label: string }>;
} {
  if (!heatmap) {
    return { weeks: [], monthLabels: [] };
  }
  const byDate = new Map(heatmap.days.map((day) => [day.date, day]));
  const firstDay = new Date(Date.UTC(heatmap.year, 0, 1));
  const startWeekday = (firstDay.getUTCDay() + 6) % 7;
  const totalDays = 365 + ((heatmap.year % 4 === 0 && (heatmap.year % 100 !== 0 || heatmap.year % 400 === 0)) ? 1 : 0);
  const cells: Array<HeatmapDay | null> = [];
  for (let i = 0; i < startWeekday; i += 1) {
    cells.push(null);
  }
  for (let i = 0; i < totalDays; i += 1) {
    const day = new Date(firstDay.getTime() + i * 86400000);
    const key = day.toISOString().slice(0, 10);
    cells.push(byDate.get(key) ?? { date: key, minutes: 0, events: 0, color: "#ebedf0" });
  }
  while (cells.length % 7 !== 0) {
    cells.push(null);
  }

  const weeks: Array<Array<HeatmapDay | null>> = [];
  for (let i = 0; i < cells.length; i += 7) {
    weeks.push(cells.slice(i, i + 7));
  }

  const monthLabels: Array<{ week: number; label: string }> = [];
  weeks.forEach((week, index) => {
    const firstCell = week.find((cell) => cell !== null);
    if (!firstCell) {
      return;
    }
    const month = new Date(firstCell.date).getUTCMonth();
    const previousCell = weeks[index - 1]?.find((cell) => cell !== null);
    const previousMonth = previousCell ? new Date(previousCell.date).getUTCMonth() : null;
    if (index === 0 || previousMonth !== month) {
      monthLabels.push({ week: index, label: replayMonthLabels[month] });
    }
  });
  return { weeks, monthLabels };
}

function LearningReplayCard({
  heatmap,
  selectedDate,
  dayDetail,
  onSelectDate,
}: {
  heatmap: Heatmap | null;
  selectedDate: string | null;
  dayDetail: DayDetail | null;
  onSelectDate: (date: string) => void;
}) {
  const grid = buildReplayGrid(heatmap);

  return (
    <Card className="border-emerald-200 bg-emerald-50/30">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="text-emerald-800">学习热力图</CardTitle>
            <CardDescription>
              {heatmap
                ? `${heatmap.year} 年 · 累计学习 ${heatmap.total_minutes} 分钟 · 活跃 ${heatmap.active_days} 天`
                : "正在加载年度学习分布"}
            </CardDescription>
          </div>
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <span>少</span>
            <span className="h-3 w-3 rounded-sm" style={{ background: "#ebedf0" }} />
            <span className="h-3 w-3 rounded-sm" style={{ background: "#9be9a8" }} />
            <span className="h-3 w-3 rounded-sm" style={{ background: "#40c463" }} />
            <span className="h-3 w-3 rounded-sm" style={{ background: "#30a14e" }} />
            <span className="h-3 w-3 rounded-sm" style={{ background: "#216e39" }} />
            <span>多</span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {heatmap ? (
          <div className="overflow-x-auto pb-1">
            <div className="inline-block min-w-full">
              <div className="flex pl-6 text-[10px] text-muted-foreground" style={{ height: 14 }}>
                {grid.monthLabels.map((month) => (
                  <div key={`replay-month-${month.week}`} style={{ position: "relative", left: `${month.week * 13}px` }}>
                    {month.label}
                  </div>
                ))}
              </div>
              <div className="flex gap-[2px]">
                <div className="flex w-6 flex-col gap-[2px] pr-1 text-[10px] text-muted-foreground">
                  {replayDayLabels.map((label, index) => (
                    <div key={`replay-day-${index}`} className="h-[12px] leading-[12px]">{label}</div>
                  ))}
                </div>
                <div className="flex gap-[2px]">
                  {grid.weeks.map((week, weekIndex) => (
                    <div key={`replay-week-${weekIndex}`} className="flex flex-col gap-[2px]">
                      {week.map((cell, dayIndex) => (
                        <button
                          type="button"
                          key={`replay-cell-${weekIndex}-${dayIndex}`}
                          title={cell ? `${cell.date} · ${cell.minutes} 分钟 · ${cell.events} 次` : ""}
                          disabled={!cell}
                          onClick={() => cell && onSelectDate(cell.date)}
                          className={`h-[12px] w-[12px] rounded-sm border transition ${
                            selectedDate === cell?.date
                              ? "border-emerald-700 ring-1 ring-emerald-500"
                              : "border-transparent hover:border-slate-400"
                          }`}
                          style={{ background: cell ? cell.color : "transparent" }}
                        />
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <Skeleton className="h-28 rounded-lg" />
        )}

        {selectedDate ? (
          <div className="rounded-lg border border-emerald-200 bg-white/70 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="font-semibold text-emerald-900">{selectedDate} 学习详情</p>
                {dayDetail ? (
                  <p className="text-xs text-muted-foreground">
                    {dayDetail.total_events} 次练习 · {dayDetail.study_minutes} 分钟 · 正确率 {dayDetail.accuracy}% · 错词 {dayDetail.mistake_count}
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground">正在加载当天学习详情...</p>
                )}
              </div>
              <Link href={`/dashboard/replay/${selectedDate}`} className="text-sm font-medium text-emerald-700 hover:underline">
                分钟级回放
              </Link>
            </div>
            {dayDetail && dayDetail.hours.length > 0 ? (
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                {dayDetail.hours.map((hour) => (
                  <div key={hour.hour} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
                    <div className="font-semibold text-slate-700">{hour.label}</div>
                    <div className="text-muted-foreground">
                      {hour.minutes.reduce((sum, minute) => sum + minute.total, 0)} 题 ·
                      拼写 {hour.minutes.reduce((sum, minute) => sum + minute.spelling, 0)} ·
                      英译中 {hour.minutes.reduce((sum, minute) => sum + minute.english_to_chinese, 0)} ·
                      中译英 {hour.minutes.reduce((sum, minute) => sum + minute.chinese_to_english, 0)}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function WordTable({ title, words, onWordClick }: { title: string; words: WordMasterySummary[]; onWordClick?: (word: string) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="ipad:text-xl">{title}</CardTitle>
        <CardDescription className="ipad:text-base">显示已进入单词级复习记录的单词，按综合优先级、错误类型和遗忘风险排序。</CardDescription>
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
                  <th className="px-3 py-2">连续</th>
                  <th className="px-3 py-2">错次</th>
                  <th className="px-3 py-2">推荐任务</th>
                  <th className="px-3 py-2">为什么复习</th>
                  <th className="px-3 py-2">复习状态</th>
                  <th className="px-3 py-2">下次复习</th>
                </tr>
              </thead>
              <tbody>
                {words.map((word) => (
                  <tr className="border-t" key={`${title}-${word.word}`}>
                    <td className="px-3 py-2 font-medium text-primary hover:underline cursor-pointer" onClick={() => onWordClick?.(word.word)}>{word.word}</td>
                    <td className="px-3 py-2">{word.status_label || statusLabels[word.status]}</td>
                    <td className="px-3 py-2">{formatPercent(word.priority_score)}</td>
                    <td className="px-3 py-2">{formatPercent(word.memory_strength)}</td>
                    <td className="px-3 py-2">{formatPercent(word.forget_risk)}</td>
                    <td className="px-3 py-2">对 {word.consecutive_correct_count} / 错 {word.consecutive_error_count}</td>
                    <td className="px-3 py-2">{word.mistake_count}</td>
                    <td className="px-3 py-2">{word.recommended_task}{word.scheduled_task_count > 0 ? `（${word.scheduled_task_count} 个）` : ""}</td>
                    <td className="px-3 py-2 max-w-[14rem] text-muted-foreground">{word.review_reason}</td>
                    <td className="px-3 py-2 max-w-[14rem] text-muted-foreground">{word.review_status_note}</td>
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

function WordHistoryModal({ history, onClose }: { history: WordHistoryResponse; onClose: () => void }) {
  const errorTypeLabels: Record<string, string> = {
    "first-letter": "首字母",
    "meaning": "词义",
    "middle": "中间结构",
    "sequence": "字母顺序",
    "ending": "词尾",
    "missing-letter": "漏字母",
    "extra-letter": "多字母",
    "unknown": "完全不会",
    "spelling": "拼写",
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-auto bg-slate-950/60 px-4 py-10" onClick={onClose}>
      <div className="relative w-full max-w-2xl rounded-lg bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold">{history.word} 的复习历史</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-slate-900 text-lg">x</button>
        </div>
        <div className="mb-4 flex gap-4 text-sm">
          <span>当前强度: {Math.round(history.current_strength * 100)}%</span>
          <span>遗忘风险: {Math.round(history.current_risk * 100)}%</span>
          <span>复习 {history.review_count} 次, 错误 {history.mistake_count} 次</span>
        </div>
        <div className="max-h-96 overflow-y-auto rounded-lg border">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted sticky top-0">
              <tr>
                <th className="px-3 py-2">时间</th>
                <th className="px-3 py-2">类型</th>
                <th className="px-3 py-2">结果</th>
                <th className="px-3 py-2">错误类型</th>
                <th className="px-3 py-2">详情</th>
              </tr>
            </thead>
            <tbody>
              {history.events.map((evt, i) => (
                <tr key={i} className="border-t">
                  <td className="px-3 py-1.5">{new Date(evt.timestamp).toLocaleString("zh-CN")}</td>
                  <td className="px-3 py-1.5">{evt.event_type === "review" ? (
                    <span className={evt.is_correct ? "text-emerald-600" : "text-red-500"}>
                      {evt.is_correct ? "正确" : "错误"}
                    </span>
                  ) : "犯错"}</td>
                  <td className="px-3 py-1.5">{evt.score != null ? `${evt.score} / 5` : "-"}</td>
                  <td className="px-3 py-1.5">{evt.error_type ? errorTypeLabels[evt.error_type] ?? evt.error_type : "-"}</td>
                  <td className="px-3 py-1.5 max-w-xs text-muted-foreground">{evt.detail ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {history.events.length === 0 ? (
            <p className="px-3 py-4 text-center text-muted-foreground">暂无历史记录。</p>
          ) : null}
        </div>
        <div className="mt-4 text-right">
          <Button onClick={onClose} variant="outline" size="sm">关闭</Button>
        </div>
      </div>
    </div>
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
  const [useSlowLearner, setUseSlowLearner] = useState(() => getModelSettings().useSlowLearnerProfile ?? false);
  const [isTogglingSlowLearner, setIsTogglingSlowLearner] = useState(false);
  const importInputRef = useRef<HTMLInputElement | null>(null);

  const [dailyReport, setDailyReport] = useState<DailyReport | null>(null);
  const [todayPlan, setTodayPlan] = useState<TodayPlan | null>(null);
  const [retentionCurve, setRetentionCurve] = useState<RetentionCurve | null>(null);
  const [reviewForecast, setReviewForecast] = useState<ReviewForecast | null>(null);
  const [pointsSummary, setPointsSummary] = useState<PointsSummary | null>(null);
  const [todayProgress, setTodayProgress] = useState<TodayProgress | null>(null);
  const [errorBreakdown, setErrorBreakdown] = useState<ErrorBreakdown | null>(null);
  const [studyStreak, setStudyStreak] = useState<StudyStreak | null>(null);
  const [learningHeatmap, setLearningHeatmap] = useState<Heatmap | null>(null);
  const [selectedReplayDate, setSelectedReplayDate] = useState<string | null>(null);
  const [selectedReplayDayDetail, setSelectedReplayDayDetail] = useState<DayDetail | null>(null);
  const [selectedCourseId] = useState<string>("");
  const [isGeneratingReport, setIsGeneratingReport] = useState(false);
  const [selectedWordHistory, setSelectedWordHistory] = useState<WordHistoryResponse | null>(null);
  const [, setIsLoadingWordHistory] = useState(false);

  useEffect(() => {
    async function loadDashboard() {
      const accessToken = getAccessToken() ?? await getFreshAccessToken();
      if (!accessToken) {
        setErrorMessage("请先登录后再查看数据看板");
        setIsLoading(false);
        return;
      }

      try {
        const courseId = selectedCourseId || undefined;
        // Phase 1: critical data (show page immediately)
        const [dashboardData, reportData, planData, curveData, errorData, streakData] = await Promise.all([
          getMemoryDashboardWithCourse(accessToken, courseId),
          getDailyReport(accessToken).catch(() => null),
          getTodayPlan(accessToken).catch(() => null),
          getRetentionCurve(accessToken, courseId).catch(() => null),
          getErrorBreakdown(accessToken).catch(() => null),
          getStudyStreak(accessToken).catch(() => null),
        ]);
        setDashboard(dashboardData);
        setDailyReport(reportData);
        setTodayPlan(planData);
        setRetentionCurve(curveData);
        setErrorBreakdown(errorData);
        setStudyStreak(streakData);
        setIsLoading(false); // Show page now!

        // Phase 2: secondary data (load after page renders, non-blocking)
        void (async () => {
          const [forecastData, pointsData, progressData, heatmapData] = await Promise.all([
            getReviewForecast(accessToken).catch(() => null),
            getPointsSummary(accessToken).catch(() => null),
            getTodayProgress(accessToken).catch(() => null),
            getHeatmap(accessToken).catch(() => null),
          ]);
          setReviewForecast(forecastData);
          setPointsSummary(pointsData);
          setTodayProgress(progressData);
          setLearningHeatmap(heatmapData);
        })();
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : "读取数据看板失败");
      } finally {
        setIsLoading(false);
      }
    }

    void loadDashboard();
  }, [selectedCourseId]);

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

  async function toggleSlowLearnerProfile() {
    const accessToken = getAccessToken();
    if (!accessToken) return;
    setIsTogglingSlowLearner(true);
    const nextValue = !useSlowLearner;
    try {
      const settings = getModelSettings();
      settings.useSlowLearnerProfile = nextValue;
      await savePersistedModelSettings(settings, accessToken);
      setUseSlowLearner(nextValue);
      setFsrsFitMessage(nextValue
        ? "已开启慢学者模式：复习间隔更短、频率更高，适合记得慢忘得快的孩子。"
        : "已关闭慢学者模式，恢复标准儿童复习调度。");
    } catch {
      setErrorMessage("慢学者模式切换失败");
    } finally {
      setIsTogglingSlowLearner(false);
    }
  }

  // Force a full page reload on the FIRST visit per browser so stale
  // chunks after a rebuild never get reused. The flag is versioned with
  // a build tag so a new deployment can invalidate previous builds by
  // bumping DASHBOARD_RELOAD_VERSION. Without the version suffix, a user
  // who already saw v1 would never reload on a v2 build, defeating the
  // purpose. The previous version wrote a permanent "dashboard_v2_loaded"
  // flag that never cleared — once tripped it would never re-trigger,
  // and on a fresh browser the user got a jarring mid-render reload.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const RELOAD_VERSION = "v3";
    const flagKey = `dashboard_reload_${RELOAD_VERSION}`;
    if (window.localStorage.getItem(flagKey) !== "1") {
      window.localStorage.setItem(flagKey, "1");
      // Clear the older-version flag so storage doesn't accumulate over
      // time across many releases.
      for (let i = 0; i < window.localStorage.length; i++) {
        const key = window.localStorage.key(i);
        if (key && key.startsWith("dashboard_reload_") && key !== flagKey) {
          window.localStorage.removeItem(key);
        }
      }
      window.location.reload();
    }
  }, []);

  async function handleGenerateReport() {
    const accessToken = getAccessToken();
    if (!accessToken) {
      return;
    }
    setIsGeneratingReport(true);
    try {
      const report = await generateDailyReport(accessToken);
      setDailyReport(report);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "生成日报失败");
    } finally {
      setIsGeneratingReport(false);
    }
  }

  async function handleWordDrillDown(word: string) {
    const accessToken = getAccessToken();
    if (!accessToken) return;
    setIsLoadingWordHistory(true);
    try {
      const history = await getWordHistory(accessToken, word);
      setSelectedWordHistory(history);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "获取单词历史失败");
    } finally {
      setIsLoadingWordHistory(false);
    }
  }

  async function handleReplayDateSelect(date: string) {
    const accessToken = getAccessToken() ?? await getFreshAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再查看学习回放");
      return;
    }
    setSelectedReplayDate(date);
    setSelectedReplayDayDetail(null);
    try {
      setSelectedReplayDayDetail(await getDayDetail(accessToken, date));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "读取学习回放失败");
    }
  }

  const fsrsFitRecommendation = dashboard ? getFsrsFitRecommendation(dashboard) : null;
  const maxErrorCount = errorBreakdown ? Math.max(...errorBreakdown.items.map((item) => Math.max(item.this_week_count, item.last_week_count)), 1) : 1;

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

        {isLoading ? (
          <div className="space-y-6 ipad:space-y-8">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
            </div>
            <Skeleton className="h-48 rounded-lg" />
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
            </div>
            <Skeleton className="h-40 rounded-lg" />
            <Skeleton className="h-64 rounded-lg" />
          </div>
        ) : null}
        {!isLoading && errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 ipad:px-6 ipad:py-4 ipad:text-base">{errorMessage}</p> : null}
        {fsrsFitMessage ? <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 ipad:px-6 ipad:py-4 ipad:text-base">{fsrsFitMessage}</p> : null}

        {!isLoading && !errorMessage && !dashboard ? (
          <Card className="border-dashed">
            <CardHeader className="text-center">
              <CardTitle className="text-xl ipad:text-2xl">暂无学习数据</CardTitle>
              <CardDescription className="ipad:text-base">
                还没有学习数据，去开始学习吧
              </CardDescription>
            </CardHeader>
            <CardContent className="flex justify-center pb-6">
              <Button asChild>
                <Link href="/learning">开始学习</Link>
              </Button>
            </CardContent>
          </Card>
        ) : null}

        {dashboard ? (
          <>
            <LearningReplayCard
              heatmap={learningHeatmap}
              selectedDate={selectedReplayDate}
              dayDetail={selectedReplayDayDetail}
              onSelectDate={handleReplayDateSelect}
            />

            {dailyReport ? (
              <Card className="border-emerald-200 bg-emerald-50/30">
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <CardTitle className="text-emerald-800">今日学习报告</CardTitle>
                    <span className="text-sm text-muted-foreground">{dailyReport.report_date}</span>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <p className="text-sm leading-relaxed text-emerald-900">{dailyReport.summary}</p>
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5">
                      正确率 {Math.round(dailyReport.accuracy_rate * 100)}%
                    </span>
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5">
                      复习 {dailyReport.review_count} 次
                    </span>
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5">
                      答对 {dailyReport.correct_count} 次
                    </span>
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5">
                      学习 {dailyReport.study_duration_minutes} 分钟
                    </span>
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5">
                      练习 {dailyReport.words_practiced} 个单词
                    </span>
                    {dailyReport.mistake_count > 0 ? (
                      <span className="rounded-full bg-red-100 px-2 py-0.5 text-red-700">
                        错词 {dailyReport.mistake_count} 个
                      </span>
                    ) : null}
                    {studyStreak ? (
                      <span className="rounded-full bg-orange-100 px-2 py-0.5">
                        连续 {studyStreak.current_streak_days} 天
                      </span>
                    ) : null}
                    {dailyReport.new_words_practiced > 0 ? (
                      <span className="rounded-full bg-blue-100 px-2 py-0.5 text-blue-700">
                        新学 {dailyReport.new_words_practiced} 个
                      </span>
                    ) : null}
                  </div>
                  {dailyReport.per_word_breakdown.length > 0 ? (
                    <div className="rounded-md border border-emerald-200 bg-white/60 p-2 text-xs">
                      <p className="mb-1 font-medium text-muted-foreground">今日复习详情（每个词复习了几遍）</p>
                      <div className="space-y-0.5">
                        {dailyReport.per_word_breakdown.map((w) => (
                          <div key={w.word} className="flex items-center justify-between gap-2">
                            <span className="font-medium text-slate-700">{w.word}</span>
                            <span className="text-muted-foreground">
                              {w.reviews} 次 · 答对 {w.correct}
                              {w.reviews > 0 && ` · ${Math.round((w.correct / w.reviews) * 100)}%`}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {dailyReport.review_count > 0 ? (
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded-md border border-blue-200 bg-blue-50/50 p-2 text-xs">
                        <p className="font-semibold text-blue-700">✍️ 拼写题</p>
                        <p className="text-lg font-bold text-blue-700">
                          {dailyReport.spelling_total} <span className="text-xs font-normal text-muted-foreground">题 · 答对 {dailyReport.spelling_correct}</span>
                        </p>
                        {dailyReport.spelling_total > 0 && (
                          <p className="text-muted-foreground">
                            正确率 {Math.round((dailyReport.spelling_correct / dailyReport.spelling_total) * 100)}%
                          </p>
                        )}
                      </div>
                      <div className="rounded-md border border-purple-200 bg-purple-50/50 p-2 text-xs">
                        <p className="font-semibold text-purple-700">🔘 选择题</p>
                        <p className="text-lg font-bold text-purple-700">
                          {dailyReport.choice_total} <span className="text-xs font-normal text-muted-foreground">题 · 答对 {dailyReport.choice_correct}</span>
                        </p>
                        {dailyReport.choice_total > 0 && (
                          <p className="text-muted-foreground">
                            正确率 {Math.round((dailyReport.choice_correct / dailyReport.choice_total) * 100)}%
                          </p>
                        )}
                      </div>
                    </div>
                  ) : null}
                  {dailyReport.per_type_breakdown.length > 0 ? (
                    <div className="rounded-md border border-slate-200 bg-slate-50/50 p-2 text-xs">
                      <p className="mb-1 font-medium text-muted-foreground">分题型明细</p>
                      <div className="space-y-0.5">
                        {dailyReport.per_type_breakdown.map((t) => (
                          <div key={t.mode} className="flex items-center justify-between gap-2">
                            <span className="text-slate-700">{t.label}</span>
                            <span className="text-muted-foreground">
                              {t.reviews} 次 · 答对 {t.correct}
                              {t.reviews > 0 && ` · ${Math.round((t.correct / t.reviews) * 100)}%`}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {dailyReport.struggling_words.length > 0 ? (
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">需要关注的单词</p>
                      <div className="flex flex-wrap gap-1.5">
                        {dailyReport.struggling_words.map((sw) => (
                          <span
                            key={sw.word}
                            className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs"
                          >
                            {sw.word}
                            <span className="text-amber-600">({sw.recommendation})</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <Button type="button" variant="outline" size="sm" onClick={handleGenerateReport} disabled={isGeneratingReport}>
                    {isGeneratingReport ? "正在生成..." : "重新生成日报"}
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed p-4">
                <p className="text-sm text-muted-foreground">今日学习报告尚未生成。</p>
                <Button type="button" variant="outline" size="sm" onClick={handleGenerateReport} disabled={isGeneratingReport}>
                  {isGeneratingReport ? "正在生成..." : "生成今日日报"}
                </Button>
              </div>
            )}

            {todayPlan ? (
              <Card>
                <CardHeader>
                  <CardTitle>今日学习计划</CardTitle>
                  <CardDescription>预计总时长 {todayPlan.total_minutes} 分钟</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {todayPlan.items.map((item) => (
                    <div key={item.task_type} className="flex items-center justify-between rounded-md bg-muted/50 px-3 py-2">
                      <div>
                        <p className="text-sm font-medium">{item.task_description}</p>
                        <p className="text-xs text-muted-foreground">约 {item.estimated_minutes} 分钟</p>
                      </div>
                      {item.item_count > 0 ? (
                        <span className="text-sm font-medium text-primary">{item.item_count} 项</span>
                      ) : null}
                    </div>
                  ))}
                </CardContent>
              </Card>
            ) : null}

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <StatCard label="已掌握单词" value={dashboard.mastered_words} hint={`共 ${dashboard.total_words} 个单词，已掌握 ${dashboard.mastered_words} 个`} />
              <StatCard label="困难/教学中单词" value={dashboard.weak_words} hint={`${dashboard.learning_words} 个正在巩固或接近掌握`} />
              <StatCard label="复习准确率" value={formatPercent(dashboard.accuracy_rate)} hint={`${dashboard.correct_reviews} / ${dashboard.total_reviews} 次复习正确`} />
              <StatCard label="下次复习" value={formatDateTime(dashboard.next_review_at)} hint={`${dashboard.due_now_count} 项已到期，${dashboard.overdue_count} 项超时`} />
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <StatCard label="平均记忆强度" value={formatPercent(dashboard.average_memory_strength)} hint="越高说明回忆越稳定" />
              <StatCard label="平均遗忘风险" value={formatPercent(dashboard.average_forget_risk)} hint="越高越需要尽快复习" />
              <StatCard label="平均复习间隔" value={`${dashboard.average_interval_days} 天`} hint="由 FSRS 稳定度和英语难度修正共同决定" />
            </div>

            {todayProgress ? (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">📋 今日学习进度</CardTitle>
                  <CardDescription>计划与实际完成对比</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {/* Review words */}
                    <div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">复习词汇</span>
                        <span className="tabular-nums">
                          <span className="text-emerald-600 font-semibold">{todayProgress.review.completed_items}</span>
                          <span className="text-muted-foreground"> / {todayProgress.review.planned}</span>
                          {todayProgress.review.remaining > 0 && (
                            <span className="ml-1 text-amber-600">（剩 {todayProgress.review.remaining}）</span>
                          )}
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${todayProgress.review.planned > 0 ? Math.min((todayProgress.review.completed_items / todayProgress.review.planned) * 100, 100) : 0}%` }} />
                      </div>
                    </div>
                    {/* New words */}
                    <div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">新词学习</span>
                        <span className="tabular-nums">
                          <span className="text-blue-600 font-semibold">{todayProgress.new_words.completed}</span>
                          <span className="text-muted-foreground"> / {todayProgress.new_words.planned}</span>
                          {todayProgress.new_words.remaining > 0 && (
                            <span className="ml-1 text-amber-600">（剩 {todayProgress.new_words.remaining}）</span>
                          )}
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${todayProgress.new_words.planned > 0 ? Math.min((todayProgress.new_words.completed / todayProgress.new_words.planned) * 100, 100) : 0}%` }} />
                      </div>
                    </div>
                    {/* Mistake practice */}
                    <div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">错词强化</span>
                        <span className="tabular-nums">
                          <span className="text-red-600 font-semibold">{todayProgress.mistakes.completed}</span>
                          <span className="text-muted-foreground"> / {todayProgress.mistakes.planned}</span>
                          {todayProgress.mistakes.remaining > 0 && (
                            <span className="ml-1 text-amber-600">（剩 {todayProgress.mistakes.remaining}）</span>
                          )}
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-red-400 transition-all" style={{ width: `${todayProgress.mistakes.planned > 0 ? Math.min((todayProgress.mistakes.completed / todayProgress.mistakes.planned) * 100, 100) : 0}%` }} />
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ) : null}

            {pointsSummary ? (
              <Card className="border-2 border-amber-200 bg-gradient-to-r from-amber-50 to-yellow-50">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-lg">🏆 我的积分</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap items-center gap-4">
                    <div className="text-center">
                      <p className="text-3xl font-bold text-amber-600">{pointsSummary.total_points}</p>
                      <p className="text-xs text-muted-foreground">总积分</p>
                    </div>
                    <div className="flex-1">
                      <p className="text-lg font-semibold">{pointsSummary.level_label}</p>
                      <p className="text-xs text-muted-foreground">今日获得 +{pointsSummary.today_points}</p>
                      {pointsSummary.next_level_points && (
                        <div className="mt-1">
                          <div className="flex justify-between text-[10px] text-muted-foreground">
                            <span>距下一级</span>
                            <span>{pointsSummary.total_points}/{pointsSummary.next_level_points}</span>
                          </div>
                          <div className="mt-0.5 h-2 overflow-hidden rounded-full bg-amber-100">
                            <div
                              className="h-full rounded-full bg-amber-500 transition-all"
                              style={{ width: `${pointsSummary.next_level_progress_pct}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="mt-3 border-t pt-3">
                    <p className="mb-1 text-xs font-medium text-muted-foreground">积分热力图</p>
                    <PointsHeatmapCompact />
                  </div>
                </CardContent>
              </Card>
            ) : null}

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
                  <p>当前模式：{dashboard.fsrs_parameters_source === "user_fitted" ? "个人拟合参数" : "内置权重"}{useSlowLearner ? " + 慢学者" : ""}</p>
                  <p>历史答题记录：{dashboard.total_reviews} / {dashboard.fsrs_min_training_reviews}</p>
                  <p>上次拟合：{formatDateTime(dashboard.fsrs_fitted_at)}</p>
                  {fsrsFitRecommendation ? (
                    <div
                      className={`mt-3 rounded-md border px-3 py-2 ${
                        fsrsFitRecommendation.tone === "ready"
                          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                          : fsrsFitRecommendation.tone === "wait"
                            ? "border-amber-200 bg-amber-50 text-amber-800"
                            : "border-slate-200 bg-slate-50 text-slate-700"
                      }`}
                    >
                      <p className="font-semibold">{fsrsFitRecommendation.title}</p>
                      <p className="mt-1">{fsrsFitRecommendation.detail}</p>
                    </div>
                  ) : null}
                </div>
                <div className="flex flex-col gap-2">
                  <Button
                    type="button"
                    variant={useSlowLearner ? "default" : "outline"}
                    onClick={toggleSlowLearnerProfile}
                    disabled={isTogglingSlowLearner}
                    className={useSlowLearner ? "bg-amber-600 hover:bg-amber-700" : ""}
                  >
                    {isTogglingSlowLearner ? "切换中..." : useSlowLearner ? "🐢 慢学者模式" : "慢学者模式"}
                  </Button>
                  <Button
                    type="button"
                    onClick={fitFsrsFromHistory}
                    disabled={isFittingFsrs || dashboard.total_reviews < dashboard.fsrs_min_training_reviews}
                  >
                    {isFittingFsrs ? "正在拟合" : dashboard.fsrs_parameters_source === "user_fitted" ? "重新拟合" : "拟合 FSRS 参数"}
                  </Button>
                </div>
              </CardContent>
            </Card>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <StatCard label="今日学习时长" value={formatDuration(dashboard.study_time.today_seconds)} hint="20 秒无键盘输入会自动暂停计时" />
              <StatCard label="本周学习时长" value={formatDuration(dashboard.study_time.week_seconds)} hint="按自然周统计活跃学习时间" />
              <StatCard label="本月学习时长" value={formatDuration(dashboard.study_time.month_seconds)} hint="按自然月统计活跃学习时间" />
              {studyStreak ? (
                <StatCard
                  label="累计学习时长"
                  value={formatDuration(dashboard.study_time.total_seconds)}
                  hint={`连续 ${studyStreak.current_streak_days} 天，共 ${studyStreak.total_study_days} 天学习，最长连续 ${studyStreak.longest_streak_days} 天${studyStreak.today_studied ? "，今天已学习" : ""}`}
                />
              ) : (
                <StatCard label="今年学习时长" value={formatDuration(dashboard.study_time.year_seconds)} hint={`累计 ${formatDuration(dashboard.study_time.total_seconds)}`} />
              )}
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

            {retentionCurve ? (() => {
              const bins = retentionCurve.bins;
              const maxReviews = Math.max(...bins.map((b) => b.total_reviews), 1);
              const CHART_W = 680;
              const CHART_H = 220;
              const PAD_L = 44;
              const PAD_R = 14;
              const PAD_T = 14;
              const PAD_B = 32;
              const plotW = CHART_W - PAD_L - PAD_R;
              const plotH = CHART_H - PAD_T - PAD_B;
              const barW = Math.max(14, plotW / bins.length - 10);
              const gapX = plotW / bins.length;

              // Y ticks for review count (left axis) — 0 to maxReviews
              const yTicks = [0, Math.round(maxReviews * 0.5), maxReviews];
              // Y ticks for recall rate (right axis) — 0% to 100%
              const recallYTicks = [0, 25, 50, 75, 100];

              // Build polyline points for the recall rate curve
              const curvePoints = bins
                .map((bin, i) => {
                  const x = PAD_L + gapX * i + gapX / 2;
                  const y = PAD_T + plotH - (bin.total_reviews > 0 ? bin.recall_rate : 0) * plotH;
                  return `${x},${y}`;
                })
                .join(" ");

              // P2-1: Detect steep recall drops (>20% between consecutive bins)
              const dropWarnings: { from: string; to: string; drop: number }[] = [];
              for (let i = 1; i < bins.length; i++) {
                if (bins[i - 1].total_reviews > 0 && bins[i].total_reviews > 0) {
                  const drop = bins[i - 1].recall_rate - bins[i].recall_rate;
                  if (drop >= 0.20) {
                    dropWarnings.push({
                      from: bins[i - 1].elapsed_days_label + "天",
                      to: bins[i].elapsed_days_label + "天",
                      drop: Math.round(drop * 100),
                    });
                  }
                }
              }

              // Bar chart: bar height = review count, curve line = accuracy rate
              return (
                <Card>
                  <CardHeader>
                    <CardTitle>记忆保持曲线</CardTitle>
                    <CardDescription>
                      横轴为距离上次复习的天数，柱高表示复习次数，曲线表示正确率。
                      {retentionCurve.course_id ? "（已按课程筛选）" : ""}
                      {dropWarnings.length > 0 && (
                        <span className="mt-1 block text-amber-600">
                          ⚠ {dropWarnings.map((w, i) => (
                            <span key={i}>{i > 0 ? "；" : ""}{w.from}→{w.to} 正确率骤降 {w.drop}%{i === dropWarnings.length - 1 ? "，建议在此时间窗口前提前复习" : ""}</span>
                          ))}
                        </span>
                      )}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto">
                      <svg
                        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
                        className="w-full"
                        style={{ minWidth: "560px", maxHeight: "260px" }}
                      >
                        {/* Horizontal grid lines + left Y labels (review count) */}
                        {yTicks.map((v) => {
                          const y = PAD_T + plotH - (v / maxReviews) * plotH;
                          return (
                            <g key={`y-${v}`}>
                              <line x1={PAD_L} y1={y} x2={CHART_W - PAD_R} y2={y} stroke="#f1f5f9" strokeWidth="1" />
                              <text x={PAD_L - 6} y={y + 4} textAnchor="end" fontSize="10" fill="#94a3b8">
                                {v}
                              </text>
                            </g>
                          );
                        })}
                        {/* Right Y labels (recall rate %) */}
                        {recallYTicks.map((pct) => {
                          const y = PAD_T + plotH - (pct / 100) * plotH;
                          return (
                            <text key={`ry-${pct}`} x={CHART_W - PAD_R + 2} y={y + 4} textAnchor="start" fontSize="10" fill="#64748b">
                              {pct > 0 ? `${pct}%` : ""}
                            </text>
                          );
                        })}

                        {/* Bars — height = review count */}
                        {bins.map((bin, i) => {
                          const barH = Math.max(bin.total_reviews > 0 ? (bin.total_reviews / maxReviews) * plotH : 0, bin.total_reviews > 0 ? 2 : 0);
                          const x = PAD_L + gapX * i + (gapX - barW) / 2;
                          const y = PAD_T + plotH - barH;
                          return (
                            <g key={`bar-${i}`}>
                              <rect
                                x={x} y={y} width={barW} height={barH}
                                rx="2" fill="#93c5fd" opacity="0.7"
                              />
                              {bin.total_reviews > 0 && (
                                <text x={x + barW / 2} y={y - 4} textAnchor="middle" fontSize="10" fill="#64748b">
                                  {bin.total_reviews}
                                </text>
                              )}
                            </g>
                          );
                        })}

                        {/* Recall rate curve (polyline) */}
                        {bins.some((b) => b.total_reviews > 0) && (
                          <polyline
                            points={curvePoints}
                            fill="none"
                            stroke="#2563eb"
                            strokeWidth="2.5"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        )}

                        {/* Curve data points */}
                        {bins.map((bin, i) => {
                          if (bin.total_reviews === 0) return null;
                          const cx = PAD_L + gapX * i + gapX / 2;
                          const cy = PAD_T + plotH - bin.recall_rate * plotH;
                          return (
                            <g key={`dot-${i}`}>
                              <circle cx={cx} cy={cy} r="4" fill="white" stroke="#2563eb" strokeWidth="2" />
                              <text x={cx} y={cy - 8} textAnchor="middle" fontSize="10" fontWeight="600" fill="#1e40af">
                                {Math.round(bin.recall_rate * 100)}%
                              </text>
                            </g>
                          );
                        })}

                        {/* X-axis labels */}
                        {bins.map((bin, i) => {
                          const x = PAD_L + gapX * i + gapX / 2;
                          return (
                            <text key={`xl-${i}`} x={x} y={CHART_H - 6} textAnchor="middle" fontSize="11" fill="#64748b">
                              {bin.elapsed_days_label}天
                            </text>
                          );
                        })}

                        {/* Axis lines */}
                        <line x1={PAD_L} y1={PAD_T} x2={PAD_L} y2={PAD_T + plotH} stroke="#e2e8f0" strokeWidth="1" />
                        <line x1={PAD_L} y1={PAD_T + plotH} x2={CHART_W - PAD_R} y2={PAD_T + plotH} stroke="#e2e8f0" strokeWidth="1" />

                        {/* Axis labels */}
                        <text x={10} y={PAD_T + plotH / 2} textAnchor="middle" fontSize="9" fill="#94a3b8" transform={`rotate(-90, 10, ${PAD_T + plotH / 2})`}>
                          复习次数
                        </text>
                        <text x={CHART_W - 4} y={PAD_T + plotH / 2} textAnchor="middle" fontSize="9" fill="#94a3b8" transform={`rotate(90, ${CHART_W - 4}, ${PAD_T + plotH / 2})`}>
                          正确率
                        </text>
                      </svg>
                    </div>
                    {/* Legend */}
                    <div className="flex justify-center gap-5 pt-2 text-[11px] text-muted-foreground">
                      <span className="flex items-center gap-1.5">
                        <span className="inline-block h-3 w-3 rounded-sm bg-blue-300 opacity-70" /> 复习次数
                      </span>
                      <span className="flex items-center gap-1.5">
                        <svg width="20" height="12"><line x1="2" y1="6" x2="18" y2="6" stroke="#2563eb" strokeWidth="2.5" strokeLinecap="round" /></svg>
                        正确率曲线
                      </span>
                    </div>
                  </CardContent>
                </Card>
              );
            })() : null}

            {reviewForecast ? (
              <Card>
                <CardHeader>
                  <CardTitle>📅 智能复习建议</CardTitle>
                  <CardDescription>基于历史学习数据和到期预测，帮助合理安排复习时间</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-4 md:grid-cols-2">
                    {/* Today */}
                    <div className="rounded-lg border bg-slate-50 p-3">
                      <p className="text-xs font-medium text-muted-foreground">今日到期</p>
                      <p className="text-2xl font-bold text-slate-900">
                        {reviewForecast.today.remaining_count} <span className="text-sm font-normal text-muted-foreground">词</span>
                      </p>
                      <p className="text-xs text-muted-foreground">
                        预计 {reviewForecast.today.remaining_minutes_low}~{reviewForecast.today.remaining_minutes_high} 分钟
                        {reviewForecast.backlog_count > reviewForecast.today.remaining_count * 2 && (
                          <span className="ml-1 text-red-500">（总积压 {reviewForecast.backlog_count} 词）</span>
                        )}
                      </p>
                    </div>
                    {/* Tomorrow */}
                    <div className="rounded-lg border bg-slate-50 p-3">
                      <p className="text-xs font-medium text-muted-foreground">明日到期</p>
                      <p className="text-2xl font-bold text-slate-900">
                        {reviewForecast.tomorrow.due_count} <span className="text-sm font-normal text-muted-foreground">词</span>
                      </p>
                      <p className="text-xs text-muted-foreground">
                        预计 {reviewForecast.tomorrow.estimated_minutes[0]}~{reviewForecast.tomorrow.estimated_minutes[1]} 分钟
                        {reviewForecast.tomorrow.high_risk_count > 0 && (
                          <span className="ml-1 text-amber-600">⚠ {reviewForecast.tomorrow.high_risk_count} 词高风险</span>
                        )}
                      </p>
                    </div>
                  </div>
                  {/* Weekly */}
                  <div className="mt-3 flex items-center gap-3 rounded-lg border bg-slate-50 p-3">
                    <div className="flex-1">
                      <p className="text-xs font-medium text-muted-foreground">本周未来 7 天</p>
                      <p className="text-lg font-semibold text-slate-900">
                        {reviewForecast.week.due_count} 词到期 · 日均 {reviewForecast.week.daily_average} 词
                      </p>
                    </div>
                    {reviewForecast.week.peak_count > 0 && (
                      <div className="text-right">
                        <p className="text-xs text-muted-foreground">最忙一天</p>
                        <p className="text-sm font-semibold text-slate-700">
                          {reviewForecast.week.peak_day} · {reviewForecast.week.peak_count} 词
                        </p>
                      </div>
                    )}
                  </div>
                  {/* Suggested daily target */}
                  <div className="mt-3 flex items-center gap-2 rounded-lg bg-blue-50 px-3 py-2">
                    <span className="text-lg">🎯</span>
                    <div>
                      <span className="text-sm font-semibold text-blue-700">今日建议学习 {reviewForecast.suggested_daily_minutes} 分钟</span>
                      <p className="text-xs text-blue-500">基于今日到期词数和历史效率计算</p>
                    </div>
                  </div>
                  {/* Load level badge */}
                  <div className="mt-3 flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">复习负载</span>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                      reviewForecast.load_level === "light" ? "bg-emerald-100 text-emerald-700" :
                      reviewForecast.load_level === "moderate" ? "bg-blue-100 text-blue-700" :
                      reviewForecast.load_level === "heavy" ? "bg-amber-100 text-amber-700" :
                      "bg-red-100 text-red-700"
                    }`}>
                      {reviewForecast.load_level === "light" ? "🟢 轻松" :
                       reviewForecast.load_level === "moderate" ? "🟡 适中" :
                       reviewForecast.load_level === "heavy" ? "🟠 繁重" : "🔴 超载"}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      每词均 {reviewForecast.efficiency.avg_seconds_per_item}s · 近7天正确率 {Math.round(reviewForecast.efficiency.recent_accuracy * 100)}%
                    </span>
                  </div>
                  {/* Suggestions */}
                  {reviewForecast.suggested_actions.length > 0 && (
                    <div className="mt-3 space-y-1.5">
                      {reviewForecast.suggested_actions.map((action, i) => (
                        <p key={i} className="flex items-start gap-1.5 text-xs text-slate-600">
                          <span className="mt-0.5 shrink-0">💡</span>
                          {action}
                        </p>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            ) : null}

            {errorBreakdown && errorBreakdown.items.length > 0 ? (
              <Card>
                <CardHeader>
                  <CardTitle>错误类型分布</CardTitle>
                  <CardDescription>
                    本周共 {errorBreakdown.total_this_week} 个错误
                    {errorBreakdown.total_last_week > 0 ? `，上周 ${errorBreakdown.total_last_week} 个` : ""}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {errorBreakdown.items.map((item) => (
                    <div className="grid grid-cols-[6rem_1fr_3rem_4rem] items-center gap-3" key={item.error_type}>
                      <span className="text-sm text-muted-foreground">{item.error_label}</span>
                      <div className="flex h-4 items-center gap-1">
                        <div
                          className="h-full rounded-sm bg-blue-400"
                          style={{ width: `${item.this_week_count > 0 ? Math.max((item.this_week_count / maxErrorCount) * 100, 2) : 0}%` }}
                          title={`本周: ${item.this_week_count}`}
                        />
                        <div
                          className="h-full rounded-sm bg-slate-300"
                          style={{ width: `${item.last_week_count > 0 ? Math.max((item.last_week_count / maxErrorCount) * 100, 2) : 0}%` }}
                          title={`上周: ${item.last_week_count}`}
                        />
                      </div>
                      <span className="text-right text-sm tabular-nums">{item.this_week_count}</span>
                      <span className="text-right text-xs tabular-nums">
                        {item.trend === "up" ? "↑ 上升" : item.trend === "down" ? "↓ 下降" : "→ 持平"}
                      </span>
                    </div>
                  ))}
                  <div className="flex items-center gap-4 pt-2 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-2.5 w-2.5 rounded-sm bg-blue-400" /> 本周
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-2.5 w-2.5 rounded-sm bg-slate-300" /> 上周
                    </span>
                  </div>
                </CardContent>
              </Card>
            ) : null}

            <div className="grid gap-4">
              <WordTable title="最需要复习的单词" words={dashboard.weakest_words} onWordClick={handleWordDrillDown} />
              <WordTable title="掌握最稳定的单词" words={dashboard.strongest_words} onWordClick={handleWordDrillDown} />
            </div>
          </>
        ) : null}

        {selectedWordHistory ? (
          <WordHistoryModal
            history={selectedWordHistory}
            onClose={() => setSelectedWordHistory(null)}
          />
        ) : null}
      </section>
    </main>
  );
}

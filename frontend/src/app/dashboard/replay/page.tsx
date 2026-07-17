"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getFreshAccessToken } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import { DayDetail, Heatmap, HeatmapDay, getDayDetail, getHeatmap } from "@/lib/learning-replay";

const DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""]; // Monday-first grid: (getUTCDay() + 6) % 7

export default function LearningReplayPage() {
  const [heatmap, setHeatmap] = useState<Heatmap | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [dayDetail, setDayDetail] = useState<DayDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      let token = getAccessToken();
      if (!token) {
        token = await getFreshAccessToken();
      }
      if (!token) {
        setLoading(false);
        return;
      }
      try {
        const hm = await getHeatmap(token);
        setHeatmap(hm);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedDate) {
      setDayDetail(null);
      return;
    }
    void (async () => {
      const token = getAccessToken() ?? await getFreshAccessToken();
      if (!token) return;
      try {
        const d = await getDayDetail(token, selectedDate);
        setDayDetail(d);
      } catch {
        setDayDetail(null);
      }
    })();
  }, [selectedDate]);

  // Build a 53-week x 7-day grid (GitHub style)
  const grid = useMemo(() => {
    if (!heatmap) return { weeks: [] as Array<Array<HeatmapDay | null>>, monthLabels: [] as Array<{ week: number; label: string }> };
    const byDate = new Map(heatmap.days.map((d) => [d.date, d]));
    const year = heatmap.year;
    const firstDay = new Date(Date.UTC(year, 0, 1));
    const startWeekday = (firstDay.getUTCDay() + 6) % 7; // Monday=0
    const totalDays = 365 + ((year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)) ? 1 : 0);
    const cells: Array<HeatmapDay | null> = [];
    for (let i = 0; i < startWeekday; i++) cells.push(null);
    for (let i = 0; i < totalDays; i++) {
      const d = new Date(firstDay.getTime() + i * 86400000);
      const key = d.toISOString().slice(0, 10);
      cells.push(byDate.get(key) || { date: key, minutes: 0, events: 0, color: "#ebedf0" });
    }
    while (cells.length % 7 !== 0) cells.push(null);
    const weeks: Array<Array<HeatmapDay | null>> = [];
    for (let i = 0; i < cells.length; i += 7) {
      weeks.push(cells.slice(i, i + 7));
    }
    // Month labels at the top of each week where a new month starts
    const monthLabels: Array<{ week: number; label: string }> = [];
    weeks.forEach((week, i) => {
      const firstCell = week.find((c) => c !== null);
      if (firstCell) {
        const month = new Date(firstCell.date).getUTCMonth();
        if (i === 0 || new Date(weeks[i - 1].find((c) => c !== null)?.date || firstCell.date).getUTCMonth() !== month) {
          monthLabels.push({ week: i, label: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][month] });
        }
      }
    });
    return { weeks, monthLabels };
  }, [heatmap]);

  if (loading) {
    return <main className="p-6 text-sm text-muted-foreground">正在加载学习热力图...</main>;
  }

  if (!heatmap) {
    return <main className="p-6 text-sm text-muted-foreground">请先登录查看学习回放。</main>;
  }

  return (
    <main className="mx-auto max-w-7xl p-4 md:p-6 space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold md:text-3xl">学习回放</h1>
          <p className="text-sm text-muted-foreground">
            {heatmap.year} 年 · 累计学习 {heatmap.total_minutes} 分钟 · 活跃 {heatmap.active_days} 天
          </p>
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
      </header>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>年度热力图</CardTitle>
          <CardDescription>点击日期查看学习详情（GitHub Contribution 风格）</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <div className="inline-block min-w-full">
            <div className="relative text-[10px] text-muted-foreground" style={{ height: 14 }}>
              {grid.monthLabels.map((m) => (
                <div key={`m-${m.week}`} style={{ position: "absolute", left: `${30 + m.week * 14}px` }}>{m.label}</div>
              ))}
            </div>
            <div className="flex gap-[2px]">
              <div className="flex w-6 flex-col gap-[2px] pr-1 text-[10px] text-muted-foreground">
                {DAY_LABELS.map((l, i) => (
                  <div key={`lbl-${i}`} className="h-[12px] leading-[12px]">{l}</div>
                ))}
              </div>
              <div className="flex gap-[2px]">
                {grid.weeks.map((week, wi) => (
                  <div key={`w-${wi}`} className="flex flex-col gap-[2px]">
                    {week.map((cell, di) => (
                      <button
                        type="button"
                        key={`c-${wi}-${di}`}
                        title={cell ? `${cell.date} · ${cell.minutes} 分钟 · ${cell.events} 次` : ""}
                        disabled={!cell}
                        onClick={() => cell && setSelectedDate(cell.date)}
                        className="h-[12px] w-[12px] rounded-sm border border-transparent hover:border-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                        style={{ background: cell ? cell.color : "transparent" }}
                      />
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {selectedDate ? (
        <Card>
          <CardHeader>
            <CardTitle>{selectedDate} 学习详情</CardTitle>
            {dayDetail ? (
              <CardDescription className="flex flex-wrap gap-3 text-xs">
                <span>📚 {dayDetail.total_events} 次练习</span>
                <span>⏱️ {dayDetail.study_minutes} 分钟</span>
                <span>✅ 正确率 {dayDetail.accuracy}%</span>
                <span>❌ 错词 {dayDetail.mistake_count}</span>
                <Link href={`/dashboard/replay/${selectedDate}`} className="text-emerald-600 hover:underline">
                  查看分钟级回放 →
                </Link>
              </CardDescription>
            ) : (
              <CardDescription>加载中...</CardDescription>
            )}
          </CardHeader>
          {dayDetail ? (
            <CardContent className="space-y-3">
              {dayDetail.day_modes && dayDetail.day_modes.length > 0 ? (
                <div className="rounded-md border border-emerald-200 bg-emerald-50/40 p-2 text-xs">
                  <p className="mb-1 font-semibold text-emerald-700">📊 今日题型分布</p>
                  <div className="grid grid-cols-2 gap-1 md:grid-cols-3">
                    {dayDetail.day_modes.map((m) => (
                      <div key={m.mode} className="flex items-center justify-between rounded border border-slate-200 bg-white px-2 py-0.5">
                        <span className="text-slate-700">{m.label}</span>
                        <span className="font-semibold tabular-nums">{m.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {dayDetail.hours.map((h) => {
                const totalSpelling = h.minutes.reduce((s, m) => s + m.spelling, 0);
                const totalChoice = h.minutes.reduce((s, m) => s + m.english_to_chinese, 0);
                const totalC2E = h.minutes.reduce((s, m) => s + m.chinese_to_english, 0);
                const totalPhrase = h.minutes.reduce((s, m) => s + m.phrase, 0);
                const totalSentence = h.minutes.reduce((s, m) => s + m.sentence, 0);
                return (
                  <div key={h.hour} className="rounded-md border border-slate-200 bg-slate-50/50 p-2 text-xs">
                    <div className="mb-1 font-semibold text-slate-700">⏰ {h.label} · {h.total_events || h.minutes.reduce((s, m) => s + m.total, 0)} 题 · 正确率 {h.accuracy || 0}% · {(h.study_minutes || 0) > 0 ? `${h.study_minutes} 分钟` : ''}</div>
                    {h.modes && h.modes.length > 0 ? (
                      <div className="mb-1 flex flex-wrap gap-1">
                        {h.modes.map((m) => (
                          <span key={m.mode} className="rounded border border-slate-200 bg-white px-1.5 py-0.5">
                            {m.label} <span className="font-semibold tabular-nums">{m.count}</span>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <div className="mb-1 text-muted-foreground">拼写 {totalSpelling} · 英译中 {totalChoice} · 中译英 {totalC2E} · 短语 {totalPhrase} · 句子 {totalSentence}</div>
                    )}
                    <div className="flex flex-wrap gap-1">
                      {h.minutes.map((m) => (
                        <Link
                          key={m.minute}
                          href={`/dashboard/replay/${selectedDate}?hour=${h.hour}&minute=${m.minute}`}
                          className="rounded border border-slate-200 bg-white px-1.5 py-0.5 tabular-nums hover:bg-emerald-50"
                        >
                          {m.minute}分·{m.total}题·{m.accuracy}%
                        </Link>
                      ))}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          ) : null}
        </Card>
      ) : null}
    </main>
  );
}

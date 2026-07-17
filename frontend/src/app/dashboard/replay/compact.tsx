"use client";

import { useEffect, useMemo, useState } from "react";

import { getFreshAccessToken } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import { Heatmap, HeatmapDay, getHeatmap } from "@/lib/learning-replay";

const DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""]; // Monday-first grid: (getUTCDay() + 6) % 7

export function LearningReplayCompact({ onSelectDate }: { onSelectDate?: (d: string) => void }) {
  const [heatmap, setHeatmap] = useState<Heatmap | null>(null);
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

  const grid = useMemo(() => {
    if (!heatmap) return { weeks: [] as Array<Array<HeatmapDay | null>>, monthLabels: [] as Array<{ week: number; label: string }> };
    const byDate = new Map(heatmap.days.map((d) => [d.date, d]));
    const year = heatmap.year;
    const firstDay = new Date(Date.UTC(year, 0, 1));
    const startWeekday = (firstDay.getUTCDay() + 6) % 7;
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
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
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
    return <p className="text-sm text-muted-foreground">正在加载学习热力图...</p>;
  }
  if (!heatmap) {
    return <p className="text-sm text-muted-foreground">请先登录查看学习热力图。</p>;
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="text-muted-foreground">
          {heatmap.year} 年 · 累计 {heatmap.total_minutes} 分钟 · 活跃 {heatmap.active_days} 天
        </div>
        <div className="flex items-center gap-1 text-muted-foreground">
          <span>少</span>
          <span className="h-3 w-3 rounded-sm" style={{ background: "#ebedf0" }} />
          <span className="h-3 w-3 rounded-sm" style={{ background: "#9be9a8" }} />
          <span className="h-3 w-3 rounded-sm" style={{ background: "#40c463" }} />
          <span className="h-3 w-3 rounded-sm" style={{ background: "#30a14e" }} />
          <span className="h-3 w-3 rounded-sm" style={{ background: "#216e39" }} />
          <span>多</span>
        </div>
      </div>
      <div className="overflow-x-auto">
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
                      onClick={() => cell && onSelectDate && onSelectDate(cell.date)}
                      className="h-[12px] w-[12px] rounded-sm border border-transparent hover:border-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                      style={{ background: cell ? cell.color : "transparent" }}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

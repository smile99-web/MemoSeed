"use client";

import { useEffect, useMemo, useState } from "react";

const DAY_LABELS = ["", "Mon", "", "Wed", "", "Fri", ""];

const POINTS_COLORS = ["#ebedf0", "#dbeafe", "#93c5fd", "#3b82f6", "#1d4ed8"];

interface PointsDay {
  date: string;
  points: number;
  color: string;
}

export function PointsHeatmapCompact() {
  const [days, setDays] = useState<PointsDay[]>([]);
  const [totalPoints, setTotalPoints] = useState(0);
  const [activeDays, setActiveDays] = useState(0);

  useEffect(() => {
    void (async () => {
      const { getAccessToken } = await import("@/lib/auth");
      const { getFreshAccessToken, fetchWithAuth, parseApiError } = await import("@/lib/api");
      const { getApiBaseUrl } = await import("@/lib/api-base-url");

      let token = getAccessToken();
      if (!token) token = await getFreshAccessToken();
      if (!token) return;

      try {
        const r = await fetchWithAuth(`${getApiBaseUrl()}/memory/points/summary`, { cache: "no-store" }, token);
        if (!r.ok) throw new Error(await parseApiError(r));
        const summary = await r.json() as { total_points: number; recent_logs: Array<{ points_changed: number; reason: string; detail: string | null; created_at: string | null }> };

        // Build daily aggregation from recent_logs + heatmap-style grid
        const byDate = new Map<string, number>();
        for (const log of summary.recent_logs) {
          if (!log.created_at) continue;
          const d = log.created_at.slice(0, 10);
          byDate.set(d, (byDate.get(d) || 0) + log.points_changed);
        }

        // Also get full year heatmap from points API
        const year = new Date().getFullYear();
        const firstDay = new Date(Date.UTC(year, 0, 1));
        const totalDays = 365 + ((year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)) ? 1 : 0);
        const result: PointsDay[] = [];
        for (let i = 0; i < totalDays; i++) {
          const d = new Date(firstDay.getTime() + i * 86400000);
          const key = d.toISOString().slice(0, 10);
          const pts = byDate.get(key) || 0;
          const absPts = Math.abs(pts);
          const color = absPts === 0 ? POINTS_COLORS[0]
            : absPts <= 10 ? POINTS_COLORS[1]
            : absPts <= 30 ? POINTS_COLORS[2]
            : absPts <= 80 ? POINTS_COLORS[3]
            : POINTS_COLORS[4];
          result.push({ date: key, points: pts, color });
        }
        setDays(result);
        setTotalPoints(summary.total_points);
        setActiveDays([...byDate.values()].filter(v => v > 0).length);
      } catch { /* */ }
    })();
  }, []);

  const grid = useMemo(() => {
    if (!days.length) return { weeks: [] as Array<Array<PointsDay | null>>, monthLabels: [] as Array<{ week: number; label: string }> };
    const year = new Date().getFullYear();
    const firstDay = new Date(Date.UTC(year, 0, 1));
    const startWeekday = (firstDay.getUTCDay() + 6) % 7;
    const cells: Array<PointsDay | null> = [];
    for (let i = 0; i < startWeekday; i++) cells.push(null);
    cells.push(...days);
    while (cells.length % 7 !== 0) cells.push(null);
    const weeks: Array<Array<PointsDay | null>> = [];
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
  }, [days]);

  if (!days.length) return null;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="text-muted-foreground">
          {new Date().getFullYear()} 年 · 累计 {totalPoints} 分 · 活跃 {activeDays} 天
        </div>
        <div className="flex items-center gap-1 text-muted-foreground">
          <span>少</span>
          {POINTS_COLORS.map((c) => (
            <span key={c} className="h-3 w-3 rounded-sm" style={{ background: c }} />
          ))}
          <span>多</span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <div className="inline-block min-w-full">
          <div className="flex pl-6 text-[10px] text-muted-foreground" style={{ height: 14 }}>
            {grid.monthLabels.map((m) => (
              <div key={`pm-${m.week}`} style={{ position: "relative", left: `${m.week * 13}px` }}>{m.label}</div>
            ))}
          </div>
          <div className="flex gap-[2px]">
            <div className="flex w-6 flex-col gap-[2px] pr-1 text-[10px] text-muted-foreground">
              {DAY_LABELS.map((l, i) => (
                <div key={`plbl-${i}`} className="h-[12px] leading-[12px]">{l}</div>
              ))}
            </div>
            <div className="flex gap-[2px]">
              {grid.weeks.map((week, wi) => (
                <div key={`pw-${wi}`} className="flex flex-col gap-[2px]">
                  {week.map((cell, di) => (
                    <div
                      key={`pc-${wi}-${di}`}
                      title={cell ? `${cell.date} · ${cell.points > 0 ? "+" : ""}${cell.points} 分` : ""}
                      className="h-[12px] w-[12px] rounded-sm border border-transparent"
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

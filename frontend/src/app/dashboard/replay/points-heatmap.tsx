"use client";

import { useEffect, useMemo, useState } from "react";

const DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""]; // Monday-first grid: (getUTCDay() + 6) % 7
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
  const [year, setYear] = useState<number>(() => new Date().getFullYear());
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedData, setSelectedData] = useState<PointsDay | null>(null);

  useEffect(() => {
    void (async () => {
      const { getAccessToken } = await import("@/lib/auth");
      const { getFreshAccessToken, fetchWithAuth, parseApiError } = await import("@/lib/api");
      const { getApiBaseUrl } = await import("@/lib/api-base-url");

      let token = getAccessToken();
      if (!token) token = await getFreshAccessToken();
      if (!token) return;

      try {
        const r = await fetchWithAuth(`${getApiBaseUrl()}/memory/points/heatmap`, { cache: "no-store" }, token);
        if (!r.ok) throw new Error(await parseApiError(r));
        const data = await r.json() as { year: number; days: PointsDay[]; total_points: number; active_days: number };
        setDays(data.days);
        setTotalPoints(data.total_points);
        setActiveDays(data.active_days);
        setYear(data.year);
      } catch { /* */ }
    })();
  }, []);

  useEffect(() => {
    if (!selectedDate) {
      setSelectedData(null);
      return;
    }
    const found = days.find((d) => d.date === selectedDate);
    setSelectedData(found || null);
  }, [selectedDate, days]);

  const grid = useMemo(() => {
    if (!days.length) return { weeks: [] as Array<Array<PointsDay | null>>, monthLabels: [] as Array<{ week: number; label: string }> };
    // Align the grid to the year the API actually returned, not "now" —
    // browsing a past year must not shift every cell by the weekday delta.
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
  }, [days, year]);

  if (!days.length) return null;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="text-muted-foreground">
          {year} 年 · 累计 {totalPoints} 分 · 活跃 {activeDays} 天
          {selectedData ? (
            <span className="ml-2 font-semibold text-amber-700">
              {selectedData.date} · {selectedData.points > 0 ? "+" : ""}{selectedData.points} 分
            </span>
          ) : (
            <span className="ml-2 text-muted-foreground">点击日期查看积分</span>
          )}
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
          <div className="relative text-[10px] text-muted-foreground" style={{ height: 14 }}>
            {grid.monthLabels.map((m) => (
              <div key={`pm-${m.week}`} style={{ position: "absolute", left: `${30 + m.week * 14}px` }}>{m.label}</div>
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
                    <button
                      type="button"
                      key={`pc-${wi}-${di}`}
                      title={cell ? `${cell.date} · ${cell.points > 0 ? "+" : ""}${cell.points} 分` : ""}
                      disabled={!cell}
                      onClick={() => cell && setSelectedDate(cell.date)}
                      className={`h-[12px] w-[12px] rounded-sm border ${selectedDate === cell?.date ? "border-amber-600 ring-2 ring-amber-300" : "border-transparent"} hover:border-slate-400 focus:outline-none`}
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

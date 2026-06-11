"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getAccessToken } from "@/lib/auth";
import { DayDetail, HourBlock, MinuteEvents, getDayDetail, getHourDetail, getMinuteEvents } from "@/lib/learning-replay";

export default function DayReplayPage() {
  const params = useParams<{ date: string }>();
  const date = params.date;
  const [dayDetail, setDayDetail] = useState<DayDetail | null>(null);
  const [selectedHour, setSelectedHour] = useState<number | null>(null);
  const [hourDetail, setHourDetail] = useState<{ hour: number; minutes: HourBlock["minutes"] } | null>(null);
  const [selectedMinute, setSelectedMinute] = useState<number | null>(null);
  const [minuteEvents, setMinuteEvents] = useState<MinuteEvents | null>(null);

  useEffect(() => {
    const token = getAccessToken();
    if (!token || !date) return;
    void (async () => {
      try {
        const d = await getDayDetail(token, date);
        setDayDetail(d);
      } catch { /* */ }
    })();
  }, [date]);

  useEffect(() => {
    if (selectedHour === null) {
      setHourDetail(null);
      return;
    }
    const token = getAccessToken();
    if (!token || !date) return;
    void (async () => {
      try {
        const h = await getHourDetail(token, date, selectedHour);
        setHourDetail(h);
      } catch { /* */ }
    })();
  }, [date, selectedHour]);

  useEffect(() => {
    if (selectedMinute === null || selectedHour === null) {
      setMinuteEvents(null);
      return;
    }
    const token = getAccessToken();
    if (!token || !date) return;
    void (async () => {
      try {
        const m = await getMinuteEvents(token, date, selectedHour, selectedMinute);
        setMinuteEvents(m);
      } catch { /* */ }
    })();
  }, [date, selectedHour, selectedMinute]);

  if (!dayDetail) {
    return <main className="p-6 text-sm text-muted-foreground">加载中...</main>;
  }

  return (
    <main className="mx-auto max-w-6xl p-4 md:p-6 space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <Link href="/dashboard/replay" className="text-emerald-600 hover:underline">← 返回热力图</Link>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{date} 学习日</CardTitle>
          <CardDescription className="flex flex-wrap gap-3 text-xs">
            <span>📚 {dayDetail.total_events} 次练习</span>
            <span>⏱️ {dayDetail.study_minutes} 分钟</span>
            <span>✅ 正确率 {dayDetail.accuracy}%</span>
            <span>❌ 错词 {dayDetail.mistake_count}</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {dayDetail.hours.length === 0 ? (
            <p className="text-sm text-muted-foreground">这一天没有学习记录。</p>
          ) : (
            dayDetail.hours.map((h) => (
              <div key={h.hour} className="rounded-md border border-slate-200 bg-slate-50/50 p-3">
                <button
                  type="button"
                  className="mb-2 text-sm font-semibold text-slate-700 hover:text-emerald-600"
                  onClick={() => setSelectedHour(selectedHour === h.hour ? null : h.hour)}
                >
                  ⏰ {h.label} · {h.minutes.reduce((s, m) => s + m.total, 0)} 题
                </button>
                {selectedHour === h.hour && hourDetail ? (
                  <div className="mt-2 space-y-2">
                    <div className="grid grid-cols-2 gap-1 text-xs md:grid-cols-3">
                      {hourDetail.minutes.map((m) => (
                        <button
                          key={m.minute}
                          type="button"
                          onClick={() => setSelectedMinute(selectedMinute === m.minute ? null : m.minute)}
                          className={`rounded border px-2 py-1 text-left tabular-nums ${
                            selectedMinute === m.minute
                              ? "border-emerald-500 bg-emerald-50"
                              : "border-slate-200 bg-white hover:border-emerald-300"
                          }`}
                        >
                          <div className="font-semibold">第 {m.minute} 分钟</div>
                          <div className="text-muted-foreground">
                            拼写 {m.spelling} · 英译中 {m.english_to_chinese} · 中译英 {m.chinese_to_english} · 短语 {m.phrase} · 句子 {m.sentence}
                          </div>
                          <div>正确率 {m.accuracy}%</div>
                        </button>
                      ))}
                    </div>
                    {selectedMinute !== null && minuteEvents ? (
                      <div className="mt-3 rounded border border-emerald-200 bg-emerald-50/50 p-3">
                        <h4 className="mb-2 text-sm font-semibold text-emerald-700">第 {selectedMinute} 分钟的练习记录</h4>
                        {minuteEvents.events.length === 0 ? (
                          <p className="text-xs text-muted-foreground">无详细事件记录</p>
                        ) : (
                          <ul className="space-y-1 text-xs">
                            {minuteEvents.events.map((e) => (
                              <li key={e.id} className={`rounded px-2 py-1 ${e.is_correct ? "bg-emerald-100" : "bg-red-100"}`}>
                                <div className="font-medium">{e.english_text} {e.chinese_text ? `(${e.chinese_text})` : ""}</div>
                                <div className="text-muted-foreground">
                                  答案：{e.response_text || "(空)"} · {e.review_mode || ""} · {e.is_correct ? "✅" : "❌"} · {e.duration_ms}ms
                                </div>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </main>
  );
}

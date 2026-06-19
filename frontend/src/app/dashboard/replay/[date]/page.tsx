"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getFreshAccessToken } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import { DayDetail, HourBlock, MinuteEvents, getDayDetail, getHourDetail, getMinuteEvents } from "@/lib/learning-replay";

const HOUR_LABELS: Record<string, string> = {
  "word-recall": "无提示拼写",
  "word-hinted": "有提示拼写",
  "word-preview": "预览拼写",
  "word-context": "句中拼写",
  "word-listen_spell": "听音拼写",
  "word-chinese_to_english": "看中拼写",
  "word-missing_letter": "缺字母",
  "word-hidden_recall": "隐藏拼写",
  "word-english_to_chinese": "英选中",
  "word-listen_choose_chinese": "听音选",
  "word-match_translation": "配对",
  "sentence-spelling": "整句",
  "sentence-cloze": "句子挖空",
  "word-spelling": "拼写",
  "word-spelling-spelling": "拼写",
};

export default function DayReplayPage() {
  const params = useParams<{ date: string }>();
  const searchParams = useSearchParams();
  const date = params.date;
  const [dayDetail, setDayDetail] = useState<DayDetail | null>(null);
  const [selectedHour, setSelectedHour] = useState<number | null>(null);
  const [hourDetail, setHourDetail] = useState<{ hour: number; minutes: HourBlock["minutes"]; minute_modes?: { minute: number; modes: Record<string, number> }[] } | null>(null);
  const [selectedMinute, setSelectedMinute] = useState<number | null>(null);
  const [minuteEvents, setMinuteEvents] = useState<MinuteEvents | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      const token = getAccessToken() ?? await getFreshAccessToken();
      if (!token || !date) {
        setLoading(false);
        return;
      }
      try {
        const d = await getDayDetail(token, date);
        setDayDetail(d);
      } catch { /* */ }
      finally {
        setLoading(false);
      }
    })();
  }, [date]);

  useEffect(() => {
    const hour = Number(searchParams.get("hour"));
    const minute = Number(searchParams.get("minute"));
    if (Number.isInteger(hour) && hour >= 0 && hour <= 23) {
      setSelectedHour(hour);
    }
    if (Number.isInteger(minute) && minute >= 0 && minute <= 59) {
      setSelectedMinute(minute);
    }
  }, [searchParams]);

  useEffect(() => {
    if (selectedHour === null) {
      setHourDetail(null);
      return;
    }
    void (async () => {
      const token = getAccessToken() ?? await getFreshAccessToken();
      if (!token || !date) return;
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
    void (async () => {
      const token = getAccessToken() ?? await getFreshAccessToken();
      if (!token || !date) return;
      try {
        const m = await getMinuteEvents(token, date, selectedHour, selectedMinute);
        setMinuteEvents(m);
      } catch { /* */ }
    })();
  }, [date, selectedHour, selectedMinute]);

  if (loading) {
    return <main className="p-6 text-sm text-muted-foreground">加载中...</main>;
  }

  if (!dayDetail) {
    return <main className="p-6 text-sm text-muted-foreground">请先登录查看学习回放。</main>;
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
                  className="mb-2 text-left text-sm font-semibold text-slate-700 hover:text-emerald-600"
                  onClick={() => setSelectedHour(selectedHour === h.hour ? null : h.hour)}
                >
                  ⏰ {h.label} · {h.total_events || h.minutes.reduce((s, m) => s + m.total, 0)} 题 · 正确率 {h.accuracy || 0}%{(h.study_minutes || 0) > 0 ? ` · ${h.study_minutes}分钟` : ''}
                </button>
                {h.modes && h.modes.length > 0 ? (
                  <div className="mb-2 flex flex-wrap gap-1 text-xs">
                    {h.modes.map((m) => (
                      <span key={m.mode} className="rounded border border-slate-200 bg-white px-1.5 py-0.5">
                        {m.label} <span className="font-semibold tabular-nums">{m.count}</span>
                      </span>
                    ))}
                  </div>
                ) : null}
                {selectedHour === h.hour && hourDetail ? (
                  <div className="mt-2 space-y-2">
                    <div className="grid grid-cols-2 gap-1 text-xs md:grid-cols-3">
                      {hourDetail.minutes.map((m) => {
                        const mm = hourDetail.minute_modes?.find((row) => row.minute === m.minute);
                        return (
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
                            {mm && Object.keys(mm.modes).length > 0 ? (
                              <div className="mt-0.5 flex flex-wrap gap-0.5">
                                {Object.entries(mm.modes).map(([mode, count]) => (
                                  <span key={mode} className="rounded bg-slate-100 px-1 py-0.5 text-[10px]">
                                    {HOUR_LABELS[mode] || mode} ×{count}
                                  </span>
                                ))}
                              </div>
                            ) : (
                              <div className="text-muted-foreground">
                                拼写 {m.spelling} · 英译中 {m.english_to_chinese} · 中译英 {m.chinese_to_english} · 短语 {m.phrase} · 句子 {m.sentence}
                              </div>
                            )}
                            <div>正确率 {m.accuracy}%</div>
                          </button>
                        );
                      })}
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

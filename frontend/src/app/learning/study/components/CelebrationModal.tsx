"use client";

import Link from "next/link";
import { memo } from "react";

import { Button } from "@/components/ui/button";
import { formatCourseDuration } from "@/lib/course-progress";

import CelebrationConfetti from "./CelebrationConfetti";

interface CelebrationSummary {
  courseName: string;
  durationSeconds: number;
  correctWordCount: number;
  encouragementChineseText: string;
  encouragementEnglishText: string;
  canContinue: boolean;
  statusMessage: string;
}

interface NextCourseTarget {
  id: string;
  name: string;
  packageId: string;
  isLocked: boolean;
}

/** 每日一测成绩单的一行：一个词在 听/义/写/说 四关的判定（null = 未测到，比如队列中途完成）。 */
interface TestReportEntry {
  word: string;
  chinese: string;
  listenOk: boolean | null;
  meaningOk: boolean | null;
  spellOk: boolean | null;
  speakOk: boolean | null;
}

interface CelebrationModalProps {
  nextCourse: NextCourseTarget | null;
  summary: CelebrationSummary;
  /** 每日一测完成后传入逐词成绩单；其余模式为 null。 */
  testReport?: TestReportEntry[] | null;
}

const CelebrationModal = memo(function CelebrationModal({ nextCourse, summary, testReport }: CelebrationModalProps) {
  const nextCourseHref = nextCourse
    ? `/learning/study?course_id=${nextCourse.id}&package_id=${nextCourse.packageId}&course_name=${encodeURIComponent(nextCourse.name)}`
    : "/learning";
  // 三关全过 = 听+义+写均 true；说（发音）是加分项，不计入全过，单独统计。
  const testAllPassCount = testReport ? testReport.filter((entry) => entry.listenOk === true && entry.meaningOk === true && entry.spellOk === true).length : 0;
  const testSpeakPassCount = testReport ? testReport.filter((entry) => entry.speakOk === true).length : 0;
  const renderGateIcon = (ok: boolean | null) =>
    ok === null ? <span className="text-slate-300">➖</span> : ok ? <span className="text-emerald-600">✅</span> : <span className="text-rose-500">❌</span>;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-slate-950/45 px-6 backdrop-blur-sm">
      <CelebrationConfetti runKey={summary.durationSeconds + summary.correctWordCount} />
      <div className="glass-card animate-pop-in relative z-10 max-h-[92dvh] w-full max-w-md overflow-y-auto p-7 text-center ipad:max-w-lg ipad:p-10">
        <div className="pointer-events-none absolute -top-20 left-1/2 h-44 w-72 -translate-x-1/2 rounded-full bg-cyan-300/35 blur-3xl" />
        <div className="relative">
          <div className="mx-auto mb-3 flex h-16 w-16 animate-float items-center justify-center rounded-2xl text-4xl icon-chip-amber icon-chip">🏆</div>
          <p className="text-sm font-bold uppercase tracking-[0.25em] text-cyan-700 ipad:text-base">Mission Complete</p>
          <h2 className="mt-2 text-3xl font-bold tracking-tight ipad:text-4xl">
            <span className="text-gradient">{summary.courseName}学习完成</span>
          </h2>
          <div className="mt-6">
            <div className="rounded-2xl border border-white/70 bg-white/70 px-4 py-4 shadow-soft ipad:px-6 ipad:py-5">
              <p className="text-xs text-muted-foreground ipad:text-sm">本次用时</p>
              <p className="mt-1 text-xl font-bold text-slate-900 ipad:text-3xl">{formatCourseDuration(summary.durationSeconds)}</p>
            </div>
          </div>
        {testReport && testReport.length > 0 ? (
          <div className="relative mt-5 rounded-2xl border border-rose-200/80 bg-rose-50/80 px-4 py-4 text-left shadow-soft ipad:mt-6 ipad:px-6 ipad:py-5">
            <p className="text-sm font-semibold text-rose-700 ipad:text-base">
              📝 每日一测成绩单：{testAllPassCount} / {testReport.length} 个词三关全过
            </p>
            <p className="mt-1 text-xs text-slate-600 ipad:text-sm">🎤 发音全过 {testSpeakPassCount} 个（加分项，不计入三关全过）</p>
            <ul className="mt-3 max-h-56 space-y-1.5 overflow-y-auto pr-1 ipad:max-h-64">
              {testReport.map((entry, index) => {
                const allPass = entry.listenOk === true && entry.meaningOk === true && entry.spellOk === true;
                return (
                  <li key={`${entry.word}-${index}`} className={`flex items-center justify-between gap-2 rounded-xl border px-3 py-1.5 text-sm ipad:text-base ${allPass ? "border-emerald-200 bg-emerald-50/80" : "border-amber-200 bg-amber-50/80"}`}>
                    <span className="min-w-0">
                      <span className="font-bold text-slate-900">{entry.word}</span>
                      <span className="ml-2 text-slate-600">{entry.chinese}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-1.5 text-xs font-bold ipad:gap-2 ipad:text-sm">
                      <span className="flex items-center gap-0.5" title="听音选中文"><span className="text-slate-500">听</span>{renderGateIcon(entry.listenOk)}</span>
                      <span className="flex items-center gap-0.5" title="看英文选中文"><span className="text-slate-500">义</span>{renderGateIcon(entry.meaningOk)}</span>
                      <span className="flex items-center gap-0.5" title="手写英文"><span className="text-slate-500">写</span>{renderGateIcon(entry.spellOk)}</span>
                      <span className="flex items-center gap-0.5" title="跟读发音"><span className="text-slate-500">说</span>{renderGateIcon(entry.speakOk)}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
            {testAllPassCount < testReport.length ? (
              <p className="mt-3 text-xs text-amber-700 ipad:text-sm">没全过的词已经自动加入复习队列，会在复习里再次练习。</p>
            ) : null}
          </div>
        ) : null}
        {nextCourse ? (
          <div className={`relative mt-5 rounded-2xl border px-4 py-4 text-left shadow-soft ipad:mt-6 ipad:px-6 ipad:py-5 ${nextCourse.isLocked ? "border-amber-200/80 bg-amber-50/80" : "border-emerald-200/80 bg-emerald-50/80"}`}>
            <p className="text-sm font-semibold text-slate-700 ipad:text-base">
              {nextCourse.isLocked ? "🔒 下一课已被锁定" : "📖 下一课"}
            </p>
            <p className="mt-1 text-base font-bold text-slate-900 ipad:text-lg">{nextCourse.name}</p>
            {nextCourse.isLocked ? (
              <p className="mt-2 text-xs text-amber-700 ipad:text-sm">请先完成前置课程并达到掌握标准后再学习此课程。</p>
            ) : (
              <p className="mt-2 text-xs text-emerald-700 ipad:text-sm">前置课程已达标，可以继续学习。</p>
            )}
          </div>
        ) : null}
        <div className="relative mt-5 rounded-2xl border border-emerald-200/80 bg-emerald-50/80 px-4 py-4 text-left shadow-soft ipad:mt-6 ipad:px-6 ipad:py-5">
          <p className="text-sm font-semibold text-emerald-700 ipad:text-base">鼓励语</p>
          <p className="mt-2 text-base font-bold text-slate-900 ipad:text-lg">{summary.encouragementChineseText}</p>
          <p className="mt-1 text-sm font-medium text-slate-700 ipad:text-base">{summary.encouragementEnglishText}</p>
          <p className="mt-3 text-xs text-muted-foreground ipad:text-sm">{summary.statusMessage}</p>
        </div>
        <div className="relative mt-6 flex flex-wrap justify-center gap-3 ipad:gap-4">
          {summary.canContinue ? (
            <Button asChild className="animate-glow-pulse ipad:text-base ipad:px-5 ipad:py-2">
              <Link href={nextCourseHref}>{nextCourse ? "学习下一课" : "返回开始学习"}</Link>
            </Button>
          ) : (
            <Button disabled type="button" className="ipad:text-base ipad:px-5 ipad:py-2">{nextCourse ? "朗读后进入下一课" : "朗读后返回"}</Button>
          )}
          <Button asChild variant="secondary" className="ipad:text-base ipad:px-5 ipad:py-2">
            <Link href="/learning">返回开始学习</Link>
          </Button>
        </div>
        </div>
      </div>
    </div>
  );
});

export default CelebrationModal;
export type { CelebrationSummary, NextCourseTarget, TestReportEntry };

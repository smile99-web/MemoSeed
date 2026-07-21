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

interface CelebrationModalProps {
  nextCourse: NextCourseTarget | null;
  summary: CelebrationSummary;
}

const CelebrationModal = memo(function CelebrationModal({ nextCourse, summary }: CelebrationModalProps) {
  const nextCourseHref = nextCourse
    ? `/learning/study?course_id=${nextCourse.id}&package_id=${nextCourse.packageId}&course_name=${encodeURIComponent(nextCourse.name)}`
    : "/learning";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-slate-950/45 px-6 backdrop-blur-sm">
      <CelebrationConfetti runKey={summary.durationSeconds + summary.correctWordCount} />
      <div className="glass-card animate-pop-in relative z-10 w-full max-w-md overflow-hidden p-7 text-center ipad:max-w-lg ipad:p-10">
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
export type { CelebrationSummary, NextCourseTarget };

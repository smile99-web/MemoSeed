import { getAuthUser } from "@/lib/auth";

export interface CourseCompletionStats {
  completedCount: number;
  totalDurationSeconds: number;
  totalCorrectWordCount: number;
  lastCompletedAt: string | null;
}

const emptyStats: CourseCompletionStats = {
  completedCount: 0,
  totalDurationSeconds: 0,
  totalCorrectWordCount: 0,
  lastCompletedAt: null,
};

function getCourseCompletionKey(courseId: string): string {
  const userId = getAuthUser()?.id ?? "anonymous";
  return `memoseed_course_completion_${userId}_${courseId}`;
}

function normalizeStats(value: Partial<CourseCompletionStats> | null): CourseCompletionStats {
  return {
    completedCount: Math.max(0, Math.floor(Number(value?.completedCount ?? 0))),
    totalDurationSeconds: Math.max(0, Math.floor(Number(value?.totalDurationSeconds ?? 0))),
    totalCorrectWordCount: Math.max(0, Math.floor(Number(value?.totalCorrectWordCount ?? 0))),
    lastCompletedAt: typeof value?.lastCompletedAt === "string" ? value.lastCompletedAt : null,
  };
}

export function loadCourseCompletionStats(courseId: string): CourseCompletionStats {
  if (!courseId || typeof window === "undefined") {
    return emptyStats;
  }

  try {
    const rawValue = window.localStorage.getItem(getCourseCompletionKey(courseId));
    return normalizeStats(rawValue ? (JSON.parse(rawValue) as Partial<CourseCompletionStats>) : null);
  } catch {
    return emptyStats;
  }
}

export function addCourseCompletion(courseId: string, durationSeconds: number, correctWordCount: number): CourseCompletionStats {
  const currentStats = loadCourseCompletionStats(courseId);
  const nextStats: CourseCompletionStats = {
    completedCount: currentStats.completedCount + 1,
    totalDurationSeconds: currentStats.totalDurationSeconds + Math.max(0, Math.round(durationSeconds)),
    totalCorrectWordCount: currentStats.totalCorrectWordCount + Math.max(0, Math.round(correctWordCount)),
    lastCompletedAt: new Date().toISOString(),
  };

  window.localStorage.setItem(getCourseCompletionKey(courseId), JSON.stringify(nextStats));
  return nextStats;
}

export function sumCourseCompletionDuration(courseIds: string[]): number {
  return courseIds.reduce((totalSeconds, courseId) => totalSeconds + loadCourseCompletionStats(courseId).totalDurationSeconds, 0);
}

export function formatCourseDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}小时${String(minutes).padStart(2, "0")}分`;
  }
  if (minutes > 0) {
    return `${minutes}分${String(seconds).padStart(2, "0")}秒`;
  }
  return `${seconds}秒`;
}

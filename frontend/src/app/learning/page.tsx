"use client";

import { BookOpen, ChevronRight, FolderOpen } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getAccessToken } from "@/lib/auth";
import { CourseCompletionStats, formatCourseDuration, loadCourseCompletionStats, sumCourseCompletionDuration } from "@/lib/course-progress";
import { Course, CoursePackage, listCoursePackages, listCourses } from "@/lib/courses";
import { LearningItem, listLearningItems } from "@/lib/learning";

interface CourseWithItems {
  course: Course;
  itemCount: number;
  previewItems: LearningItem[];
  completionStats: CourseCompletionStats;
}

const previewLimit = 3;

export default function LearningStartPage() {
  const [packages, setPackages] = useState<CoursePackage[]>([]);
  const [selectedPackageId, setSelectedPackageId] = useState("");
  const [courseRows, setCourseRows] = useState<CourseWithItems[]>([]);
  const [isLoadingPackages, setIsLoadingPackages] = useState(true);
  const [isLoadingCourses, setIsLoadingCourses] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [packageDurationSeconds, setPackageDurationSeconds] = useState<Record<string, number>>({});

  const packagesRef = useRef<CoursePackage[]>([]);
  packagesRef.current = packages;
  const selectedPackageIdRef = useRef("");
  selectedPackageIdRef.current = selectedPackageId;

  const selectedPackage = useMemo(
    () => packages.find((coursePackage) => coursePackage.id === selectedPackageId) ?? null,
    [packages, selectedPackageId],
  );

  useEffect(() => {
    async function loadPackages() {
      const accessToken = getAccessToken();
      if (!accessToken) {
        setErrorMessage("请先登录后再开始学习。");
        setIsLoadingPackages(false);
        return;
      }

      setIsLoadingPackages(true);
      setErrorMessage(null);
      try {
        const nextPackages = await listCoursePackages(accessToken);
        setPackages(nextPackages);
        const packageDurations = await Promise.all(
          nextPackages.map(async (coursePackage) => {
            const packageCourses = await listCourses(accessToken, coursePackage.id);
            return [coursePackage.id, sumCourseCompletionDuration(packageCourses.map((course) => course.id))] as const;
          }),
        );
        setPackageDurationSeconds(Object.fromEntries(packageDurations));
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : "读取课程包失败");
      } finally {
        setIsLoadingPackages(false);
      }
    }

    void loadPackages();
  }, []);

  useEffect(() => {
    async function loadPackageCourses(packageId: string) {
      const accessToken = getAccessToken();
      if (!accessToken) {
        setErrorMessage("请先登录后再开始学习。");
        return;
      }

      setIsLoadingCourses(true);
      setErrorMessage(null);
      try {
        const nextCourses = await listCourses(accessToken, packageId);
        const nextRows = await Promise.all(
          nextCourses.map(async (course) => {
            const items = await listLearningItems(accessToken, course.id);
            return {
              course,
              itemCount: items.length,
              previewItems: items.slice(0, previewLimit),
              completionStats: loadCourseCompletionStats(course.id),
            };
          }),
        );
        setCourseRows(nextRows);
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : "读取课程内容失败");
        setCourseRows([]);
      } finally {
        setIsLoadingCourses(false);
      }
    }

    if (!selectedPackageId) {
      setCourseRows([]);
      return;
    }

    void loadPackageCourses(selectedPackageId);
  }, [selectedPackageId]);

  const refreshCompletionStats = useCallback(function refreshCompletionStats() {
    // Re-read per-course stats from localStorage so the UI reflects
    // completions from the study session even when the page wasn't remounted.
    setCourseRows((current) => {
      if (current.length === 0) {
        return current;
      }
      let totalSeconds = 0;
      const refreshed = current.map((row) => {
        const stats = loadCourseCompletionStats(row.course.id);
        totalSeconds += stats.totalDurationSeconds;
        return { ...row, completionStats: stats };
      });
      const pkgId = selectedPackageIdRef.current;
      if (pkgId) {
        setPackageDurationSeconds((prev) => ({ ...prev, [pkgId]: totalSeconds }));
      }
      return refreshed;
    });
  }, []);

  useEffect(() => {
    function handleVisibilityChange() {
      if (document.visibilityState === "visible") {
        refreshCompletionStats();
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [refreshCompletionStats]);

  return (
    <main className="min-h-screen px-6 py-10 ipad:px-8 ipad:py-14">
      <section className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Link className="text-sm font-medium text-primary hover:underline ipad:text-base" href="/">
              返回首页
            </Link>
            <h1 className="mt-4 text-3xl font-bold tracking-tight ipad:text-4xl">开始学习</h1>
            <p className="mt-2 text-muted-foreground ipad:text-lg">先选择课程包，再选择课程进入拼写学习。</p>
          </div>
          <Button asChild variant="secondary" className="ipad:text-lg ipad:px-6 ipad:py-3">
            <Link href="/learning/import">导入/管理课程</Link>
          </Button>
        </div>

        {errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 ipad:px-6 ipad:py-4 ipad:text-base">{errorMessage}</p> : null}

        <div className="grid gap-6 ipad:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.45fr)]">
          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-xl font-semibold tracking-tight ipad:text-2xl">课程包</h2>
              <span className="text-sm text-muted-foreground ipad:text-base">{packages.length} 个</span>
            </div>

            {isLoadingPackages ? (
              <Card>
                <CardContent className="pt-6">
                  <p className="text-sm text-muted-foreground">正在加载课程包...</p>
                </CardContent>
              </Card>
            ) : packages.length > 0 ? (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                {packages.map((coursePackage) => {
                  const isSelected = coursePackage.id === selectedPackageId;
                  return (
                    <button
                      className={`rounded-lg border bg-card p-4 text-left shadow-sm transition-colors hover:border-primary ipad:p-5 ${
                        isSelected ? "border-primary ring-2 ring-primary/20" : ""
                      }`}
                      key={coursePackage.id}
                      type="button"
                      onClick={() => setSelectedPackageId(coursePackage.id)}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex min-w-0 gap-3">
                          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary ipad:h-12 ipad:w-12">
                            <FolderOpen className="h-5 w-5 ipad:h-6 ipad:w-6" />
                          </span>
                          <div className="min-w-0">
                            <h3 className="truncate text-base font-semibold ipad:text-lg">{coursePackage.name}</h3>
                            <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted-foreground ipad:text-base">
                              {coursePackage.description || "暂无课程包说明"}
                            </p>
                            <p className="mt-2 text-sm font-medium text-slate-700 ipad:text-base">
                              总时长：{formatCourseDuration(packageDurationSeconds[coursePackage.id] ?? 0)}
                            </p>
                          </div>
                        </div>
                        <ChevronRight className={`mt-2 h-5 w-5 shrink-0 ${isSelected ? "text-primary" : "text-muted-foreground"}`} />
                      </div>
                    </button>
                  );
                })}
              </div>
            ) : (
              <Card>
                <CardHeader>
                  <CardTitle>还没有课程包</CardTitle>
                  <CardDescription>先导入或创建课程包后，再回到这里开始学习。</CardDescription>
                </CardHeader>
                <CardContent>
                  <Button asChild>
                    <Link href="/learning/import">去导入课程</Link>
                  </Button>
                </CardContent>
              </Card>
            )}
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold tracking-tight ipad:text-2xl">课程内容</h2>
                <p className="mt-1 text-sm text-muted-foreground ipad:text-base">
                  {selectedPackage ? selectedPackage.name : "点击左侧课程包后显示课程"}
                </p>
              </div>
              <span className="text-sm text-muted-foreground ipad:text-base">{courseRows.length} 门</span>
            </div>

            {!selectedPackageId ? (
              <Card>
                <CardContent className="pt-6">
                  <p className="text-sm text-muted-foreground">请选择一个课程包。</p>
                </CardContent>
              </Card>
            ) : isLoadingCourses ? (
              <Card>
                <CardContent className="pt-6">
                  <p className="text-sm text-muted-foreground">正在加载课程内容...</p>
                </CardContent>
              </Card>
            ) : courseRows.length > 0 ? (
              <div className="grid gap-4 md:grid-cols-2">
                {courseRows.map(({ course, itemCount, previewItems, completionStats }) => (
                  <Link
                    className={`rounded-lg border bg-card p-5 shadow-sm transition-colors hover:border-primary ipad:p-6 ${
                      itemCount === 0 ? "pointer-events-none opacity-60" : ""
                    }`}
                    href={itemCount > 0 ? `/learning/study?course_id=${course.id}&package_id=${course.package_id}&course_name=${encodeURIComponent(course.name)}` : "#"}
                    key={course.id}
                    aria-disabled={itemCount === 0}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="truncate text-lg font-semibold ipad:text-xl">{course.name}</h3>
                        <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted-foreground ipad:text-base">
                          {course.description || "暂无课程说明"}
                        </p>
                      </div>
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary ipad:h-12 ipad:w-12">
                        <BookOpen className="h-5 w-5 ipad:h-6 ipad:w-6" />
                      </span>
                    </div>

                    <div className="mt-4 flex items-center justify-between gap-3 text-sm ipad:text-base">
                      <span className="font-medium text-foreground">{itemCount} 条学习内容</span>
                      <span className="text-primary ipad:font-semibold">{itemCount > 0 ? "进入学习" : "暂无内容"}</span>
                    </div>

                    <div className="mt-4 grid grid-cols-2 gap-3 border-t pt-4 text-sm">
                      <div>
                        <p className="text-xs text-muted-foreground">完成次数</p>
                        <p className="mt-1 font-semibold text-slate-900">{completionStats.completedCount} 次</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">总时长</p>
                        <p className="mt-1 font-semibold text-slate-900">{formatCourseDuration(completionStats.totalDurationSeconds)}</p>
                      </div>
                    </div>

                    {previewItems.length > 0 ? (
                      <div className="mt-4 space-y-2 border-t pt-4">
                        {previewItems.map((item) => (
                          <p className="truncate text-sm text-muted-foreground" key={item.id}>
                            {item.english_text} / {item.chinese_text}
                          </p>
                        ))}
                      </div>
                    ) : null}
                  </Link>
                ))}
              </div>
            ) : (
              <Card>
                <CardHeader>
                  <CardTitle>这个课程包还没有课程</CardTitle>
                  <CardDescription>去导入页面创建课程并上传内容后，就可以从这里进入学习。</CardDescription>
                </CardHeader>
                <CardContent>
                  <Button asChild>
                    <Link href="/learning/import">去创建课程</Link>
                  </Button>
                </CardContent>
              </Card>
            )}
          </section>
        </div>
      </section>
    </main>
  );
}

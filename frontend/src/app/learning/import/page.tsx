"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getAccessToken } from "@/lib/auth";
import { Course, CoursePackage, createCourse, createCoursePackage, deleteCourse, deleteCoursePackage, exportCoursePackage, importCoursePackage, listCoursePackages, listCourses, PackageExportData, PackageImportResult } from "@/lib/courses";
import { CourseCacheRebuildProgress, CourseCacheStatus, CourseCacheItemStatus, getCourseCacheStatus, LearningImportProgress, LearningImportResponse, LearningItem, listLearningItems, rebuildCourseCache, retryItemCache, uploadLearningItems, validateImportFile } from "@/lib/learning";
import { ModelSettings, getModelSettings, loadPersistedModelSettings } from "@/lib/model-settings";

const itemTypeLabels: Record<LearningItem["item_type"], string> = {
  word: "单词",
  phrase: "短语",
  sentence: "句子",
};

interface DirectoryFormState {
  packageName: string;
  packageDescription: string;
  courseName: string;
  courseDescription: string;
}

interface DeleteTarget {
  type: "package" | "course";
  id: string;
  name: string;
}

export default function LearningImportPage() {
  const [packages, setPackages] = useState<CoursePackage[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [selectedPackageId, setSelectedPackageId] = useState("");
  const [selectedCourseId, setSelectedCourseId] = useState("");
  const [form, setForm] = useState<DirectoryFormState>({
    packageName: "",
    packageDescription: "",
    courseName: "",
    courseDescription: "",
  });
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<LearningImportResponse | null>(null);
  const [items, setItems] = useState<LearningItem[]>([]);
  const [isCreatingPackage, setIsCreatingPackage] = useState(false);
  const [isCreatingCourse, setIsCreatingCourse] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [deletingPackageId, setDeletingPackageId] = useState<string | null>(null);
  const [deletingCourseId, setDeletingCourseId] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isImportingPackage, setIsImportingPackage] = useState(false);
  const [importPackageResult, setImportPackageResult] = useState<PackageImportResult | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [importProgress, setImportProgress] = useState<LearningImportProgress | null>(null);
  const [isRebuildingCache, setIsRebuildingCache] = useState(false);
  const [retryProgress, setRetryProgress] = useState<{current: number; total: number; message: string} | null>(null);

  async function handleRetryAllFailed() {
    const accessToken = getAccessToken();
    if (!accessToken || !selectedCourseId || !cacheStatus) return;
    const failedItems = cacheStatus.items.filter((s) =>
      !s.sentence_chinese_translation_ready ||
      !s.sentence_english_audio_ready ||
      !s.sentence_chinese_audio_ready ||
      !s.word_translations_ready ||
      !s.word_english_audio_ready ||
      !s.word_chinese_audio_ready
    );
    if (failedItems.length === 0) {
      setErrorMessage("没有需要重试的项目，所有缓存已完成。");
      return;
    }
    setErrorMessage(null);
    setRetryProgress({ current: 0, total: failedItems.length, message: "开始重试..." });
    let successCount = 0;
    let failCount = 0;
    for (let i = 0; i < failedItems.length; i++) {
      setRetryProgress({ current: i + 1, total: failedItems.length, message: `正在重试 ${i + 1}/${failedItems.length}...` });
      try {
        await retryItemCache(selectedCourseId, failedItems[i].learning_item_id, accessToken, modelSettings);
        successCount++;
      } catch {
        failCount++;
      }
    }
    await refreshCacheStatus(selectedCourseId);
    setRetryProgress(null);
    setErrorMessage(failCount > 0 ? `重试完成: ${successCount} 项成功, ${failCount} 项失败` : `全部 ${successCount} 项重试成功!`);
  }
  const [cacheRebuildProgress, setCacheRebuildProgress] = useState<CourseCacheRebuildProgress | null>(null);
  const [cacheStatus, setCacheStatus] = useState<CourseCacheStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(getModelSettings());

  const selectedPackage = packages.find((coursePackage) => coursePackage.id === selectedPackageId) ?? null;
  const selectedCourse = courses.find((course) => course.id === selectedCourseId) ?? null;

  async function loadPackages() {
    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再学习");
      setIsLoading(false);
      return;
    }

    try {
      const nextPackages = await listCoursePackages(accessToken);
      setPackages(nextPackages);
      setSelectedPackageId((current) => current || nextPackages[0]?.id || "");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "读取课程包失败");
    } finally {
      setIsLoading(false);
    }
  }

  async function loadCourses(packageId: string) {
    const accessToken = getAccessToken();
    if (!accessToken || !packageId) {
      setCourses([]);
      setSelectedCourseId("");
      return;
    }

    try {
      const nextCourses = await listCourses(accessToken, packageId);
      setCourses(nextCourses);
      setSelectedCourseId((current) => (nextCourses.some((course) => course.id === current) ? current : nextCourses[0]?.id || ""));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "读取课程失败");
    }
  }

  async function refreshItems(courseId: string) {
    const accessToken = getAccessToken();
    if (!accessToken || !courseId) {
      setItems([]);
      return;
    }

    try {
      const nextItems = await listLearningItems(accessToken, courseId);
      setItems(nextItems);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "读取学习内容失败");
    }
  }

  async function refreshCacheStatus(courseId: string) {
    const accessToken = getAccessToken();
    if (!accessToken || !courseId) {
      setCacheStatus(null);
      return;
    }

    try {
      setCacheStatus(await getCourseCacheStatus(courseId, accessToken));
    } catch {
      setCacheStatus(null);
    }
  }

  useEffect(() => {
    void loadPackages();
    void loadPersistedModelSettings().then(setModelSettings);
  }, []);

  useEffect(() => {
    void loadCourses(selectedPackageId);
    setItems([]);
  }, [selectedPackageId]);

  useEffect(() => {
    void refreshItems(selectedCourseId);
    void refreshCacheStatus(selectedCourseId);
    setCacheRebuildProgress(null);
  }, [selectedCourseId]);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setResult(null);
    setErrorMessage(validateImportFile(file));
  }

  async function handleCreatePackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const accessToken = getAccessToken();
    const name = form.packageName.trim();
    if (!accessToken) {
      setErrorMessage("请先登录后再创建课程包");
      return;
    }
    if (!name) {
      setErrorMessage("请输入课程包名称");
      return;
    }

    setIsCreatingPackage(true);
    setErrorMessage(null);
    try {
      const createdPackage = await createCoursePackage(accessToken, {
        name,
        description: form.packageDescription.trim(),
      });
      setPackages((current) => [createdPackage, ...current]);
      setSelectedPackageId(createdPackage.id);
      setSelectedCourseId("");
      setCourses([]);
      setForm((current) => ({ ...current, packageName: "", packageDescription: "" }));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "创建课程包失败");
    } finally {
      setIsCreatingPackage(false);
    }
  }

  function requestDeletePackage(packageId: string) {
    const packageName = packages.find((coursePackage) => coursePackage.id === packageId)?.name ?? "当前课程包";
    setDeleteTarget({ type: "package", id: packageId, name: packageName });
  }

  async function handleDeletePackage(packageId: string) {
    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再删除课程包");
      return;
    }

    setDeletingPackageId(packageId);
    setErrorMessage(null);
    try {
      await deleteCoursePackage(accessToken, packageId);
      setPackages((current) => {
        const nextPackages = current.filter((coursePackage) => coursePackage.id !== packageId);
        setSelectedPackageId(nextPackages[0]?.id || "");
        return nextPackages;
      });
      setCourses([]);
      setSelectedCourseId("");
      setItems([]);
      setResult(null);
      setDeleteTarget(null);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "删除课程包失败");
    } finally {
      setDeletingPackageId(null);
    }
  }

  async function handleCreateCourse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const accessToken = getAccessToken();
    const name = form.courseName.trim();
    if (!accessToken) {
      setErrorMessage("请先登录后再创建课程");
      return;
    }
    if (!selectedPackageId) {
      setErrorMessage("请先选择或创建课程包");
      return;
    }
    if (!name) {
      setErrorMessage("请输入课程名称");
      return;
    }

    setIsCreatingCourse(true);
    setErrorMessage(null);
    try {
      const createdCourse = await createCourse(accessToken, {
        package_id: selectedPackageId,
        name,
        description: form.courseDescription.trim(),
      });
      setCourses((current) => [createdCourse, ...current]);
      setSelectedCourseId(createdCourse.id);
      setForm((current) => ({ ...current, courseName: "", courseDescription: "" }));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "创建课程失败");
    } finally {
      setIsCreatingCourse(false);
    }
  }

  function requestDeleteCourse(courseId: string) {
    const courseName = courses.find((course) => course.id === courseId)?.name ?? "当前课程";
    setDeleteTarget({ type: "course", id: courseId, name: courseName });
  }

  async function handleDeleteCourse(courseId: string) {
    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再删除课程");
      return;
    }

    setDeletingCourseId(courseId);
    setErrorMessage(null);
    try {
      await deleteCourse(accessToken, courseId);
      setCourses((current) => {
        const nextCourses = current.filter((course) => course.id !== courseId);
        setSelectedCourseId(nextCourses[0]?.id || "");
        return nextCourses;
      });
      setItems([]);
      setResult(null);
      setDeleteTarget(null);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "删除课程失败");
    } finally {
      setDeletingCourseId(null);
    }
  }

  async function handleExportPackage() {
    const accessToken = getAccessToken();
    if (!accessToken || !selectedPackageId || !selectedPackage) {
      setErrorMessage("请先选择课程包");
      return;
    }

    setIsExporting(true);
    setErrorMessage(null);
    try {
      await exportCoursePackage(accessToken, selectedPackageId, selectedPackage.name);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "导出课程包失败");
    } finally {
      setIsExporting(false);
    }
  }

  async function handleImportPackage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    if (!file) return;

    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再导入课程包");
      return;
    }

    setIsImportingPackage(true);
    setErrorMessage(null);
    setImportPackageResult(null);
    try {
      const text = await file.text();
      const data = JSON.parse(text) as PackageExportData;
      const result = await importCoursePackage(accessToken, data);
      setImportPackageResult(result);
      await loadPackages();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "导入课程包失败");
    } finally {
      setIsImportingPackage(false);
      event.target.value = "";
    }
  }

  function handleConfirmDelete() {
    if (!deleteTarget) {
      return;
    }

    if (deleteTarget.type === "package") {
      void handleDeletePackage(deleteTarget.id);
      return;
    }

    void handleDeleteCourse(deleteTarget.id);
  }

  async function handleImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedCourseId) {
      setErrorMessage("请先选择或创建课程");
      return;
    }

    const validationMessage = validateImportFile(selectedFile);
    if (validationMessage) {
      setErrorMessage(validationMessage);
      return;
    }

    const file = selectedFile;
    if (!file) {
      setErrorMessage("请选择 TXT 或 Excel 文件");
      return;
    }

    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再导入学习内容");
      return;
    }

    setIsUploading(true);
    setErrorMessage(null);
    setResult(null);
    setImportProgress({ percent: 5, message: "准备导入..." });

    try {
      const response = await uploadLearningItems(file, selectedCourseId, accessToken, modelSettings, {
        onProgress: setImportProgress,
      });
      setResult(response);
      setImportProgress({ percent: 95, message: "正在刷新当前课程内容..." });
      await refreshItems(selectedCourseId);
      await refreshCacheStatus(selectedCourseId);
      setImportProgress({ percent: 100, message: "导入完成" });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "导入失败");
      setImportProgress(null);
    } finally {
      setIsUploading(false);
    }
  }

  async function handleRebuildCourseCache() {
    if (!selectedCourseId) {
      setErrorMessage("请先选择课程");
      return;
    }

    const accessToken = getAccessToken();
    if (!accessToken) {
      setErrorMessage("请先登录后再重新生成缓存");
      return;
    }

    setIsRebuildingCache(true);
    setErrorMessage(null);
    setCacheRebuildProgress({
      status: "running",
      percent: 1,
      message: "准备重新生成课程缓存...",
      stats: {
        items: items.length,
        sentence_translations: 0,
        term_translations: 0,
        speech_cached: 0,
        speech_missing: 0,
        errors: 0,
      },
    });

    try {
      await rebuildCourseCache(selectedCourseId, accessToken, modelSettings, setCacheRebuildProgress);
      await refreshItems(selectedCourseId);
      await refreshCacheStatus(selectedCourseId);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "重新生成课程缓存失败");
    } finally {
      setIsRebuildingCache(false);
    }
  }

  function getItemCacheStatus(itemId: string): CourseCacheItemStatus | null {
    return cacheStatus?.items.find((item) => item.learning_item_id === itemId) ?? null;
  }

  function renderCacheBadge(label: string, ready: boolean | undefined) {
    const isReady = Boolean(ready);
    return (
      <span className={`rounded-full px-2 py-1 text-xs font-bold ${isReady ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
        {label}{isReady ? "已生成" : "未生成"}
      </span>
    );
  }

  return (
    <main className="min-h-screen px-6 py-10 ipad:px-8 ipad:py-14">
      {deleteTarget ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-6">
          <div className="glass-card w-full max-w-md p-6 ipad:max-w-lg ipad:p-8">
            <h2 className="text-xl font-bold tracking-tight ipad:text-2xl">确认删除</h2>
            <p className="mt-3 text-sm leading-6 text-muted-foreground ipad:text-base">
              确认删除{deleteTarget.type === "package" ? "课程包" : "课程"}“{deleteTarget.name}”吗？
              {deleteTarget.type === "package" ? "删除后会同步删除该课程包下的所有课程和学习内容。" : "删除后会同步删除该课程下的所有学习内容。"}
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <Button
                disabled={Boolean(deletingPackageId || deletingCourseId)}
                onClick={() => setDeleteTarget(null)}
                type="button"
                variant="outline"
              >
                取消
              </Button>
              <Button disabled={Boolean(deletingPackageId || deletingCourseId)} onClick={handleConfirmDelete} type="button">
                {deletingPackageId || deletingCourseId ? "删除中..." : "确认删除"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      <section className="mx-auto max-w-6xl space-y-6 ipad:space-y-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Link className="text-sm font-medium text-primary hover:underline ipad:text-base" href="/">
              返回首页
            </Link>
            <h1 className="mt-4 text-3xl font-bold tracking-tight ipad:text-4xl"><span className="text-gradient">课程目录与学习内容</span></h1>
            <p className="mt-2 text-muted-foreground ipad:text-lg">按“课程包 → 课程 → 导入内容”的结构管理学习资料，学习入口已独立到开始学习页面。</p>
          </div>
          <Button asChild variant="secondary" className="ipad:text-lg ipad:px-6 ipad:py-3">
            <Link href="/learning">去开始学习</Link>
          </Button>
        </div>

        {errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 ipad:px-6 ipad:py-4 ipad:text-base">{errorMessage}</p> : null}

        <div className="grid gap-6 ipad:grid-cols-2 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>1. 创建课程包</CardTitle>
              <CardDescription>给课程包取名字，并简要说明课程包内容。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <form className="space-y-3" onSubmit={handleCreatePackage}>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  placeholder="课程包名称，例如：小学英语基础"
                  value={form.packageName}
                  onChange={(event) => setForm((current) => ({ ...current, packageName: event.target.value }))}
                />
                <textarea
                  className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
                  placeholder="简要说明这个课程包的内容"
                  value={form.packageDescription}
                  onChange={(event) => setForm((current) => ({ ...current, packageDescription: event.target.value }))}
                />
                <Button disabled={isCreatingPackage} type="submit">
                  {isCreatingPackage ? "创建中..." : "创建课程包"}
                </Button>
              </form>

              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="course-package">
                  选择课程包
                </label>
                <select
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  disabled={isLoading || packages.length === 0}
                  id="course-package"
                  value={selectedPackageId}
                  onChange={(event) => setSelectedPackageId(event.target.value)}
                >
                  <option value="">{isLoading ? "正在加载..." : "请选择课程包"}</option>
                  {packages.map((coursePackage) => (
                    <option key={coursePackage.id} value={coursePackage.id}>
                      {coursePackage.name}
                    </option>
                  ))}
                </select>
                {selectedPackage ? (
                  <div className="space-y-3 rounded-lg border bg-secondary/40 p-3">
                    <p className="text-sm text-muted-foreground">{selectedPackage.description || "暂无课程包说明"}</p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        disabled={isExporting}
                        onClick={handleExportPackage}
                        type="button"
                        variant="secondary"
                      >
                        {isExporting ? "导出中..." : "导出课程包"}
                      </Button>
                      <label>
                        <input
                          accept=".json"
                          className="hidden"
                          disabled={isImportingPackage}
                          onChange={handleImportPackage}
                          type="file"
                        />
                        <Button asChild disabled={isImportingPackage} type="button" variant="secondary">
                          <span>{isImportingPackage ? "导入中..." : "导入课程包"}</span>
                        </Button>
                      </label>
                      <Button
                        disabled={deletingPackageId === selectedPackage.id}
                        onClick={() => requestDeletePackage(selectedPackage.id)}
                        type="button"
                        variant="outline"
                      >
                        {deletingPackageId === selectedPackage.id ? "删除中..." : "删除已创建课程包"}
                      </Button>
                    </div>
                    {importPackageResult ? (
                      <p className="text-sm text-green-700">
                        已导入课程包「{importPackageResult.imported_package_name}」，{importPackageResult.courses_count} 个课程，{importPackageResult.items_count} 条学习内容。
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>2. 创建课程</CardTitle>
              <CardDescription>在选中的课程包下创建课程，并说明课程具体内容。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <form className="space-y-3" onSubmit={handleCreateCourse}>
                <input
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  disabled={!selectedPackageId}
                  placeholder="课程名称，例如：三年级上册 Unit 1"
                  value={form.courseName}
                  onChange={(event) => setForm((current) => ({ ...current, courseName: event.target.value }))}
                />
                <textarea
                  className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
                  disabled={!selectedPackageId}
                  placeholder="说明这个课程的具体内容"
                  value={form.courseDescription}
                  onChange={(event) => setForm((current) => ({ ...current, courseDescription: event.target.value }))}
                />
                <Button disabled={!selectedPackageId || isCreatingCourse} type="submit">
                  {isCreatingCourse ? "创建中..." : "创建课程"}
                </Button>
              </form>

              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="course">
                  选择课程
                </label>
                <select
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
                  disabled={!selectedPackageId || courses.length === 0}
                  id="course"
                  value={selectedCourseId}
                  onChange={(event) => setSelectedCourseId(event.target.value)}
                >
                  <option value="">请选择课程</option>
                  {courses.map((course) => (
                    <option key={course.id} value={course.id}>
                      {course.name}
                    </option>
                  ))}
                </select>
                {selectedCourse ? (
                  <div className="space-y-3 rounded-lg border bg-secondary/40 p-3">
                    <p className="text-sm text-muted-foreground">{selectedCourse.description || "暂无课程说明"}</p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        disabled={isRebuildingCache || items.length === 0}
                        onClick={handleRebuildCourseCache}
                        type="button"
                        variant="secondary"
                      >
                        {isRebuildingCache ? "重新生成中..." : "重新生成课程缓存"}
                      </Button>
                      <Button
                      disabled={deletingCourseId === selectedCourse.id || isRebuildingCache}
                      onClick={() => requestDeleteCourse(selectedCourse.id)}
                      type="button"
                      variant="outline"
                    >
                      {deletingCourseId === selectedCourse.id ? "删除中..." : "删除已创建课程"}
                    </Button>
                    </div>
                    {cacheRebuildProgress ? (
                      <div className="space-y-2 rounded-md border bg-background p-3" role="status" aria-live="polite">
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span>{cacheRebuildProgress.message}</span>
                          <span>{cacheRebuildProgress.percent}%</span>
                        </div>
                        <div className="h-2 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-emerald-500 transition-all duration-300"
                            style={{ width: `${cacheRebuildProgress.percent}%` }}
                          />
                        </div>
                        <p className="text-xs text-muted-foreground">
                          句子释义 {cacheRebuildProgress.stats.sentence_translations}，单词/词组释义 {cacheRebuildProgress.stats.term_translations}，发音缓存 {cacheRebuildProgress.stats.speech_cached}，未缓存 {cacheRebuildProgress.stats.speech_missing}，错误 {cacheRebuildProgress.stats.errors}
                        </p>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>3. 导入课程内容</CardTitle>
              <CardDescription>选择 TXT 或 Excel，把内容导入到当前课程。</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-5" onSubmit={handleImport}>
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="learning-file">
                    TXT / Excel 文件
                  </label>
                  <input
                    id="learning-file"
                    type="file"
                    accept=".txt,.xlsx"
                    className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm file:mr-4 file:rounded-md file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-primary-foreground"
                    onChange={handleFileChange}
                  />
                </div>

                {selectedFile ? <p className="text-sm text-muted-foreground">已选择：{selectedFile.name}</p> : null}

                <Button disabled={!selectedCourseId || isUploading} type="submit">
                  {isUploading ? "导入中..." : "开始导入"}
                </Button>
                {importProgress ? (
                  <div className="space-y-2" role="status" aria-live="polite">
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>{importProgress.message}</span>
                      <span>{importProgress.percent}%</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary transition-all duration-300"
                        style={{ width: `${importProgress.percent}%` }}
                      />
                    </div>
                    {isUploading ? (
                      <p className="text-xs text-muted-foreground">如果文件里缺少中文释义，系统会尝试调用当前 LLM 配置补全；模型不可用时会自动跳过并返回原因。</p>
                    ) : null}
                  </div>
                ) : null}
              </form>
            </CardContent>
          </Card>
        </div>

        {result ? (
          <Card>
            <CardHeader>
              <CardTitle>本次导入结果</CardTitle>
              <CardDescription>
                共读取 {result.total_rows} 行，成功导入 {result.imported_count} 条，跳过 {result.skipped_count} 条。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {result.skipped_items.length > 0 ? (
                <ul className="space-y-2 text-sm text-muted-foreground">
                  {result.skipped_items.slice(0, 20).map((item, index) => (
                    <li className="rounded-md border px-3 py-2" key={`${item.english_text}-${index}`}>
                      {item.english_text || "空行"}：{item.reason}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">没有跳过内容。</p>
              )}
            </CardContent>
          </Card>
        ) : null}

        {cacheStatus ? (
          <Card>
            <CardHeader>
              <CardTitle>当前课程缓存状态</CardTitle>
              <CardDescription>绿色表示已经保存到数据库并可直接复用，黄色表示还没有生成或生成失败。</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 text-sm ipad:grid-cols-3 lg:grid-cols-6">
                <div className="rounded-lg border bg-secondary/30 p-3">
                  <p className="font-bold">句子中文释义</p>
                  <p className="text-muted-foreground">{cacheStatus.summary.sentence_translations_ready} / {cacheStatus.summary.total_items}</p>
                </div>
                <div className="rounded-lg border bg-secondary/30 p-3">
                  <p className="font-bold">句子英文发音</p>
                  <p className="text-muted-foreground">{cacheStatus.summary.sentence_english_audio_ready} / {cacheStatus.summary.total_items}</p>
                </div>
                <div className="rounded-lg border bg-secondary/30 p-3">
                  <p className="font-bold">句子中文发音</p>
                  <p className="text-muted-foreground">{cacheStatus.summary.sentence_chinese_audio_ready} / {cacheStatus.summary.total_items}</p>
                </div>
                <div className="rounded-lg border bg-secondary/30 p-3">
                  <p className="font-bold">单词/词组释义</p>
                  <p className="text-muted-foreground">{cacheStatus.summary.term_translations_ready} / {cacheStatus.summary.total_terms}</p>
                </div>
                <div className="rounded-lg border bg-secondary/30 p-3">
                  <p className="font-bold">单词英文发音</p>
                  <p className="text-muted-foreground">{cacheStatus.summary.word_english_audio_ready} / {cacheStatus.summary.total_terms}</p>
                </div>
                <div className="rounded-lg border bg-secondary/30 p-3">
                  <p className="font-bold">单词中文发音</p>
                  <p className="text-muted-foreground">{cacheStatus.summary.word_chinese_audio_ready} / {cacheStatus.summary.total_terms}</p>
                </div>
              </div>
              <div className="mt-4 flex items-center gap-3">
                <Button
                  variant="outline"
                  disabled={retryProgress !== null}
                  onClick={() => { void handleRetryAllFailed(); }}
                >
                  {retryProgress ? "正在重试..." : "🔄 重试所有黄色项目"}
                </Button>
                {retryProgress ? (
                  <span className="text-sm text-muted-foreground">{retryProgress.message}</span>
                ) : null}
              </div>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>当前课程已上传内容</CardTitle>
            <CardDescription>只显示当前选择课程下保存的学习内容。</CardDescription>
          </CardHeader>
          <CardContent>
            {items.length > 0 ? (
              <div className="overflow-hidden rounded-lg border">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="px-3 py-2">类型</th>
                      <th className="px-3 py-2">英文</th>
                      <th className="px-3 py-2">中文</th>
                      <th className="px-3 py-2">缓存状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const itemCacheStatus = getItemCacheStatus(item.id);
                      return (
                        <tr className="border-t" key={item.id}>
                          <td className="px-3 py-2">{itemTypeLabels[item.item_type]}</td>
                          <td className="px-3 py-2">{item.english_text}</td>
                          <td className="px-3 py-2">{item.chinese_text}</td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              {renderCacheBadge("句子中文", itemCacheStatus?.sentence_chinese_translation_ready)}
                              {renderCacheBadge("句子英文发音", itemCacheStatus?.sentence_english_audio_ready)}
                              {renderCacheBadge("句子中文发音", itemCacheStatus?.sentence_chinese_audio_ready)}
                              {renderCacheBadge("单词释义", itemCacheStatus?.word_translations_ready)}
                              {renderCacheBadge("单词英文发音", itemCacheStatus?.word_english_audio_ready)}
                              {renderCacheBadge("单词中文发音", itemCacheStatus?.word_chinese_audio_ready)}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">暂无已上传内容。</p>
            )}
          </CardContent>
        </Card>
      </section>
    </main>
  );
}

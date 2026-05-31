import { fetchWithAuth, parseApiError } from "@/lib/api";
import { getApiBaseUrl } from "@/lib/api-base-url";

export interface ReviewScorePayload {
  learning_item_id: string;
  score: number;
  review_mode: string;
  response_text?: string;
  duration_seconds?: number;
}

export interface WordMasterySummary {
  word: string;
  status: "difficult" | "teaching" | "consolidating" | "near_mastered" | "mastered";
  status_label: string;
  memory_strength: number;
  forget_risk: number;
  priority_score: number;
  review_count: number;
  mistake_count: number;
  consecutive_correct_count: number;
  consecutive_error_count: number;
  recall_correct_count: number;
  hinted_correct_count: number;
  preview_correct_count: number;
  context_correct_count: number;
  hidden_recall_correct_count: number;
  no_hint_correct_date_count: number;
  dominant_error_type: string | null;
  review_reason: string;
  recommended_task: string;
  scheduled_task_count: number;
  interval_days: number;
  next_review_at: string | null;
}

export interface WordHistoryEvent {
  timestamp: string;
  event_type: "review" | "mistake";
  score: number | null;
  is_correct: boolean | null;
  error_type: string | null;
  memory_strength: number | null;
  detail: string | null;
}

export interface WordHistoryResponse {
  word: string;
  events: WordHistoryEvent[];
  current_strength: number;
  current_risk: number;
  review_count: number;
  mistake_count: number;
}

export interface ReviewBucket {
  label: string;
  count: number;
}

export interface StudyTimeSummary {
  today_seconds: number;
  week_seconds: number;
  month_seconds: number;
  year_seconds: number;
  total_seconds: number;
}

export interface CourseProgressStats {
  course_id: string;
  completed_count: number;
  total_duration_seconds: number;
  total_correct_word_count: number;
  last_completed_at: string | null;
}

export interface MemoryDashboard {
  total_items: number;
  total_words: number;
  mastered_words: number;
  learning_words: number;
  weak_words: number;
  due_now_count: number;
  overdue_count: number;
  average_memory_strength: number;
  average_forget_risk: number;
  average_interval_days: number;
  total_reviews: number;
  correct_reviews: number;
  accuracy_rate: number;
  total_mistakes: number;
  unresolved_mistakes: number;
  fsrs_parameters_source: "built_in" | "user_fitted";
  fsrs_min_training_reviews: number;
  fsrs_training_review_count: number;
  fsrs_training_pair_count: number;
  fsrs_fitted_at: string | null;
  next_review_at: string | null;
  study_time: StudyTimeSummary;
  review_buckets: ReviewBucket[];
  weakest_words: WordMasterySummary[];
  strongest_words: WordMasterySummary[];
}

export interface FsrsFitResponse {
  fitted_at: string;
  training_review_count: number;
  training_pair_count: number;
  accuracy_rate: number;
  weights: number[];
}

export async function scheduleMemoryReview(accessToken: string, payload: ReviewScorePayload): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/memory/schedule`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        duration_seconds: payload.duration_seconds ?? 0,
      }),
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function getMemoryDashboard(accessToken: string): Promise<MemoryDashboard> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/dashboard`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as MemoryDashboard;
}

export async function fitFsrsParameters(accessToken: string): Promise<FsrsFitResponse> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/memory/fsrs/fit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as FsrsFitResponse;
}

export async function recordStudyTime(accessToken: string, durationSeconds: number, courseId?: string): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/memory/study-time`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify({
        course_id: courseId || null,
        duration_seconds: Math.max(1, Math.min(Math.round(durationSeconds), 3600)),
      }),
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function recordCourseCompletion(accessToken: string, courseId: string, durationSeconds: number, correctWordCount: number): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/memory/course-completions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        course_id: courseId,
        duration_seconds: Math.max(0, Math.round(durationSeconds)),
        correct_word_count: Math.max(0, Math.round(correctWordCount)),
      }),
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function listCourseProgressStats(accessToken: string, packageId?: string): Promise<CourseProgressStats[]> {
  const searchParams = new URLSearchParams();
  if (packageId) {
    searchParams.set("package_id", packageId);
  }
  const query = searchParams.toString();
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/course-stats${query ? `?${query}` : ""}`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as CourseProgressStats[];
}

export interface StrugglingWordItem {
  word: string;
  priority_score: number;
  memory_strength: number;
  mistake_count: number;
  error_type: string | null;
  recommendation: string;
}

export interface DailyReport {
  report_date: string;
  review_count: number;
  correct_count: number;
  accuracy_rate: number;
  study_duration_minutes: number;
  words_practiced: number;
  mistake_count: number;
  streak_days: number;
  struggling_words: StrugglingWordItem[];
  summary: string;
  next_day_strategy: Record<string, unknown>;
}

export interface TodayPlanItem {
  task_type: string;
  task_description: string;
  estimated_minutes: number;
  item_count: number;
}

export interface TodayPlan {
  plan_date: string;
  total_minutes: number;
  due_review_count: number;
  new_words_ready: number;
  unresolved_mistake_count: number;
  items: TodayPlanItem[];
  time_budget: Record<string, number>;
}

export interface RetentionBin {
  elapsed_days_label: string;
  elapsed_days: number;
  total_reviews: number;
  correct_reviews: number;
  recall_rate: number;
}

export interface RetentionCurve {
  bins: RetentionBin[];
  course_id: string | null;
}

export interface ErrorBreakdownItem {
  error_type: string;
  error_label: string;
  this_week_count: number;
  last_week_count: number;
  trend: string;
}

export interface ErrorBreakdown {
  items: ErrorBreakdownItem[];
  total_this_week: number;
  total_last_week: number;
}

export interface StudyStreak {
  current_streak_days: number;
  longest_streak_days: number;
  streak_start_date: string | null;
  total_study_days: number;
  today_studied: boolean;
}

export async function getDailyReport(accessToken: string, reportDate?: string): Promise<DailyReport> {
  const searchParams = new URLSearchParams();
  if (reportDate) {
    searchParams.set("report_date", reportDate);
  }
  const query = searchParams.toString();
  const response = await fetchWithAuth(`${getApiBaseUrl()}/reports/daily${query ? `?${query}` : ""}`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as DailyReport;
}

export async function generateDailyReport(accessToken: string, reportDate?: string): Promise<DailyReport> {
  const searchParams = new URLSearchParams();
  if (reportDate) {
    searchParams.set("report_date", reportDate);
  }
  const query = searchParams.toString();
  const response = await fetchWithAuth(`${getApiBaseUrl()}/reports/daily/generate${query ? `?${query}` : ""}`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as DailyReport;
}

export async function getTodayPlan(accessToken: string): Promise<TodayPlan> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/reports/plans/today`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as TodayPlan;
}

export async function getRetentionCurve(accessToken: string, courseId?: string): Promise<RetentionCurve> {
  const searchParams = new URLSearchParams();
  if (courseId) {
    searchParams.set("course_id", courseId);
  }
  const query = searchParams.toString();
  const response = await fetchWithAuth(`${getApiBaseUrl()}/reports/retention-curve${query ? `?${query}` : ""}`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as RetentionCurve;
}

export async function getErrorBreakdown(accessToken: string): Promise<ErrorBreakdown> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/reports/error-breakdown`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as ErrorBreakdown;
}

export async function getStudyStreak(accessToken: string): Promise<StudyStreak> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/reports/streak`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as StudyStreak;
}

export async function getMemoryDashboardWithCourse(accessToken: string, courseId?: string): Promise<MemoryDashboard> {
  const searchParams = new URLSearchParams();
  if (courseId) {
    searchParams.set("course_id", courseId);
  }
  const query = searchParams.toString();
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/dashboard${query ? `?${query}` : ""}`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as MemoryDashboard;
}

export async function getWordHistory(accessToken: string, word: string): Promise<WordHistoryResponse> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/reports/word-history/${encodeURIComponent(word)}`,
    { cache: "no-store" },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as WordHistoryResponse;
}

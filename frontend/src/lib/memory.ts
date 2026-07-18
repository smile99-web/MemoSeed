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
  review_status_note: string;
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
  status_counts?: Record<string, number>;
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

export interface PerWordBreakdownItem {
  word: string;
  reviews: number;
  correct: number;
}

export interface PerTypeBreakdownItem {
  mode: string;
  label: string;
  reviews: number;
  correct: number;
  kind: string;
}

export interface DailyReport {
  report_date: string;
  review_count: number;
  correct_count: number;
  accuracy_rate: number;
  study_duration_minutes: number;
  words_practiced: number;
  new_words_practiced: number;
  per_word_breakdown: PerWordBreakdownItem[];
  per_type_breakdown: PerTypeBreakdownItem[];
  spelling_total: number;
  spelling_correct: number;
  choice_total: number;
  choice_correct: number;
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

export interface ReviewForecastToday {
  remaining_count: number;
  remaining_minutes_low: number;
  remaining_minutes_high: number;
}

export interface ReviewForecastTomorrow {
  due_count: number;
  estimated_minutes: [number, number];
  high_risk_count: number;
}

export interface ReviewForecastWeek {
  due_count: number;
  daily_average: number;
  peak_day: string;
  peak_count: number;
}

export interface ReviewForecastEfficiency {
  avg_seconds_per_item: number;
  recent_accuracy: number;
  avg_daily_minutes: number;
}

export interface ReviewForecast {
  backlog_count: number;
  suggested_daily_minutes: number;
  today: ReviewForecastToday;
  tomorrow: ReviewForecastTomorrow;
  week: ReviewForecastWeek;
  load_level: string;
  suggested_actions: string[];
  efficiency: ReviewForecastEfficiency;
}

export interface TodayProgressRow {
  planned: number;
  completed: number;
  remaining: number;
}

export interface TodayProgressReviews {
  planned: number;
  completed_items: number;
  completed_reviews: number;
  remaining: number;
}

export interface TodayProgress {
  review: TodayProgressReviews;
  new_words: TodayProgressRow;
  mistakes: TodayProgressRow;
}

export async function rotateFocusWord(accessToken: string, learningItemId: string): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/memory/focus-rotate?learning_item_id=${learningItemId}`,
    { method: "POST" },
    accessToken,
  );
  // Without this check, a backend error (401 after refresh exhaustion,
  // 5xx, validation error) is silently swallowed by the caller's
  // `.catch(() => {})` chain — the UI shows "next batch" even when the
  // rotation never actually happened server-side.
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function getTodayProgress(accessToken: string): Promise<TodayProgress> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/today-progress`, { cache: "no-store" }, accessToken);
  if (!response.ok) throw new Error(await parseApiError(response));
  return (await response.json()) as TodayProgress;
}

export async function getReviewForecast(accessToken: string): Promise<ReviewForecast> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/review-forecast`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as ReviewForecast;
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

// ── Points & Rewards ──

export interface PointsLogEntry {
  points_changed: number;
  reason: string;
  detail: string | null;
  created_at: string | null;
}

export interface PointsSummary {
  total_points: number;
  level: number;
  level_label: string;
  today_points: number;
  next_level_points: number | null;
  next_level_progress_pct: number;
  recent_logs: PointsLogEntry[];
}

export async function getPointsSummary(accessToken: string): Promise<PointsSummary> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/points/summary`, { cache: "no-store" }, accessToken);
  if (!response.ok) throw new Error(await parseApiError(response));
  return (await response.json()) as PointsSummary;
}

// P12: effectiveness metrics (parent-facing "is the reform working?" panel)
export interface EffectivenessMetrics {
  weekly_accuracy: Array<{ week: string; reviews: number; accuracy: number }>;
  mastered_time_share: number;
  mastered_time_share_target: number;
  status_counts: Record<string, number>;
  queue_health: { due_now: number; overdue_1d: number; avg_word_interval_days: number };
  intervention_words: Array<{ word: string; attempts_14d: number; accuracy_14d: number; status: string }>;
}

export async function getEffectivenessMetrics(accessToken: string): Promise<EffectivenessMetrics> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/effectiveness`, { cache: "no-store" }, accessToken);
  if (!response.ok) throw new Error(await parseApiError(response));
  return (await response.json()) as EffectivenessMetrics;
}

export async function awardPoints(accessToken: string, pointsChange: number, reason: string, detail?: string, learningItemId?: string): Promise<void> {
  await fetchWithAuth(`${getApiBaseUrl()}/memory/points/award`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ points_change: pointsChange, reason, detail, learning_item_id: learningItemId }),
  }, accessToken);
}

export interface ReviewAdvice {
  has_recommendations: boolean;
  recommended_words: string[];
  reasoning: string;
  suggested_mode?: string;
  priority_bands?: {
    urgent?: string[];
    high?: string[];
    medium?: string[];
    low?: string[];
  };
}

export async function getReviewAdvice(accessToken: string): Promise<ReviewAdvice> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/review-advice`, { cache: "no-store" }, accessToken);
  if (!response.ok) throw new Error(await parseApiError(response));
  return (await response.json()) as ReviewAdvice;
}

export async function generateReviewAdvice(accessToken: string): Promise<ReviewAdvice> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/memory/review-advice`, {
    method: "POST",
    cache: "no-store",
  }, accessToken);
  if (!response.ok) throw new Error(await parseApiError(response));
  return (await response.json()) as ReviewAdvice;
}

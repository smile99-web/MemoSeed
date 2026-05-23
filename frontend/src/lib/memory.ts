import { refreshAccessToken } from "@/lib/auth";
import { getApiBaseUrl } from "@/lib/api-base-url";

let refreshTokenPromise: Promise<string | null> | null = null;

interface ApiErrorResponse {
  detail?: string;
}

export interface ReviewScorePayload {
  learning_item_id: string;
  score: number;
  review_mode: string;
  response_text?: string;
  duration_seconds?: number;
}

export interface WordMasterySummary {
  word: string;
  status: "mastered" | "learning" | "weak";
  memory_strength: number;
  forget_risk: number;
  review_count: number;
  mistake_count: number;
  interval_days: number;
  next_review_at: string | null;
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

async function getFreshAccessToken(): Promise<string | null> {
  refreshTokenPromise ??= refreshAccessToken().finally(() => {
    refreshTokenPromise = null;
  });
  return refreshTokenPromise;
}

async function parseApiError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorResponse;
    return body.detail ?? `请求失败：${response.status}`;
  } catch {
    return `请求失败：${response.status}`;
  }
}

async function fetchWithAuth(input: RequestInfo | URL, init: RequestInit, accessToken: string): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${accessToken}`);

  let response = await fetch(input, { ...init, headers });
  if (response.status !== 401) {
    return response;
  }

  const refreshedAccessToken = await getFreshAccessToken();
  if (!refreshedAccessToken) {
    return response;
  }

  headers.set("Authorization", `Bearer ${refreshedAccessToken}`);
  response = await fetch(input, { ...init, headers });
  return response;
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

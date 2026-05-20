import { refreshAccessToken } from "@/lib/auth";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
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
  next_review_at: string | null;
  study_time: StudyTimeSummary;
  review_buckets: ReviewBucket[];
  weakest_words: WordMasterySummary[];
  strongest_words: WordMasterySummary[];
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
    `${apiBaseUrl}/memory/schedule`,
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
  const response = await fetchWithAuth(`${apiBaseUrl}/memory/dashboard`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as MemoryDashboard;
}

export async function recordStudyTime(accessToken: string, durationSeconds: number, courseId?: string): Promise<void> {
  const response = await fetchWithAuth(
    `${apiBaseUrl}/memory/study-time`,
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

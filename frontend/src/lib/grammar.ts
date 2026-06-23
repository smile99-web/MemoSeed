import { getApiBaseUrl } from "@/lib/api-base-url";
import { getAccessToken } from "@/lib/auth";

/**
 * Grammar practice API client.
 *
 * Mirrors the backend schemas in `backend/app/schemas/grammar.py`. Keep
 * the two in sync — the frontend relies on `type` being exactly
 * "choice" or "fill_in_blank" for its render branch.
 */

export type GrammarQuestionType = "choice" | "fill_in_blank";

export interface GrammarQuestion {
  id: string;
  type: GrammarQuestionType;
  level: number;
  prompt: string;
  translation: string | null;
  options: string[] | null;
  answer: string;
  explanation: string;
}

export interface GrammarQuestionSet {
  level: number;
  questions: GrammarQuestion[];
}

export interface GrammarLevelDistribution {
  level: number;
  choice: number;
  fill_in_blank: number;
}

export interface GrammarLevelsInfo {
  min: number;
  max: number;
  questions_per_set: number;
  distribution: GrammarLevelDistribution[];
}

export interface GrammarSessionSummary {
  id: string;
  level: number;
  total_questions: number;
  correct_count: number;
  choice_questions: number;
  fill_in_questions: number;
  started_at: string;
  completed_at: string | null;
  accuracy: number;
}

export interface GrammarLevelStat {
  level: number;
  total_sessions: number;
  total_questions: number;
  correct_count: number;
  accuracy: number;
  avg_time_per_question_ms: number;
}

export interface GrammarHistoryResponse {
  recent_sessions: GrammarSessionSummary[];
  per_level: GrammarLevelStat[];
  total_sessions: number;
  total_questions: number;
  overall_accuracy: number;
}

async function fetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const accessToken = getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        detail = body.detail;
      }
    } catch {
      // body wasn't JSON — keep the status-based detail
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function getGrammarLevels(): Promise<GrammarLevelsInfo> {
  return fetchJson<GrammarLevelsInfo>("/grammar/levels");
}

export async function generateGrammarQuestions(level: number): Promise<GrammarQuestionSet> {
  return fetchJson<GrammarQuestionSet>("/grammar/generate", {
    method: "POST",
    body: JSON.stringify({ level }),
  });
}

export async function startGrammarSession(payload: {
  level: number;
  total_questions: number;
  choice_questions: number;
  fill_in_questions: number;
  question_ids: string[];
}): Promise<GrammarSessionSummary> {
  return fetchJson<GrammarSessionSummary>("/grammar/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function submitGrammarAnswer(
  sessionId: string,
  payload: {
    question_id: string;
    question_type: GrammarQuestionType;
    level: number;
    prompt: string;
    user_answer: string;
    correct_answer: string;
    is_correct: boolean;
    time_spent_ms: number;
  },
): Promise<GrammarSessionSummary> {
  return fetchJson<GrammarSessionSummary>(`/grammar/sessions/${sessionId}/answers`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function completeGrammarSession(sessionId: string): Promise<GrammarSessionSummary> {
  return fetchJson<GrammarSessionSummary>(`/grammar/sessions/${sessionId}/complete`, {
    method: "POST",
  });
}

export async function getGrammarHistory(): Promise<GrammarHistoryResponse> {
  return fetchJson<GrammarHistoryResponse>("/grammar/history");
}

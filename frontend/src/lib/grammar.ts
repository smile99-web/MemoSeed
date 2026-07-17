import { apiRequest } from "@/lib/api";
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

async function fetchJson<T>(path: string, init: { method?: "GET" | "POST"; body?: object } = {}): Promise<T> {
  // apiRequest handles 401 → refresh-token → retry and parses error bodies.
  // The previous raw fetch meant an expired access token killed grammar
  // practice until a manual re-login.
  return apiRequest<T, object>(path, {
    method: init.method ?? "GET",
    body: init.body,
    accessToken: getAccessToken() ?? undefined,
  });
}

export async function getGrammarLevels(): Promise<GrammarLevelsInfo> {
  return fetchJson<GrammarLevelsInfo>("/grammar/levels");
}

export async function generateGrammarQuestions(level: number): Promise<GrammarQuestionSet> {
  return fetchJson<GrammarQuestionSet>("/grammar/generate", {
    method: "POST",
    body: { level },
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
    body: payload,
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
    body: payload,
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

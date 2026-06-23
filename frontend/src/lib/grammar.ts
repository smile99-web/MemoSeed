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

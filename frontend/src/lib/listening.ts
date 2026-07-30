import { apiRequest } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

/**
 * Listening stories （听力故事） API client.
 *
 * Mirrors `backend/app/api/v1/listening/router.py`. Stories are AI-generated
 * from the child's practiced words; each sentence carries deterministic
 * tts-cache URLs (warmed server-side) so playback is zero-latency.
 */

export interface ListeningStorySummary {
  id: string;
  title: string;
  theme: string;
  sentence_count: number;
}

export interface ListeningStorySentence {
  en: string;
  zh: string;
  en_audio_url: string;
  zh_audio_url: string;
}

export interface ListeningStoryPayload {
  id: string;
  title: string;
  theme: string;
  sentences: ListeningStorySentence[];
}

export interface ListeningWarmStats {
  cached: number;
  generated: number;
  failed: number;
  total: number;
}

async function fetchJson<T>(path: string, init: { method?: "GET" | "POST" } = {}): Promise<T> {
  return apiRequest<T, object>(path, {
    method: init.method ?? "GET",
    accessToken: getAccessToken() ?? undefined,
  });
}

export async function getListeningStories(): Promise<ListeningStorySummary[]> {
  const data = await fetchJson<{ stories: ListeningStorySummary[] }>("/listening/stories");
  return data.stories;
}

export async function getListeningStory(storyId: string): Promise<ListeningStoryPayload> {
  return fetchJson<ListeningStoryPayload>(`/listening/stories/${storyId}`);
}

export async function warmListeningStory(storyId: string): Promise<ListeningWarmStats> {
  return fetchJson<ListeningWarmStats>(`/listening/stories/${storyId}/warm`, { method: "POST" });
}

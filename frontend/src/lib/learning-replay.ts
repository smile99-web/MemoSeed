export interface HeatmapDay {
  date: string;
  minutes: number;
  events: number;
  color: string;
}

export interface Heatmap {
  year: number;
  days: HeatmapDay[];
  total_minutes: number;
  active_days: number;
}

export interface MinuteBreakdown {
  minute: number;
  spelling: number;
  english_to_chinese: number;
  chinese_to_english: number;
  phrase: number;
  sentence: number;
  total: number;
  correct: number;
  incorrect: number;
  accuracy: number;
  study_seconds: number;
}

export interface HourBlock {
  hour: number;
  label: string;
  minutes: MinuteBreakdown[];
  modes: Array<{ mode: string; label: string; count: number }>;
  total_events: number;
  accuracy: number;
  study_minutes: number;
}

export interface ModeCount {
  mode: string;
  label: string;
  count: number;
}

export interface MinuteModeRow {
  minute: number;
  modes: Record<string, number>;
}

export interface DayDetail {
  date: string;
  study_minutes: number;
  total_events: number;
  accuracy: number;
  mistake_count: number;
  hours: HourBlock[];
  day_modes: ModeCount[];
}

export interface LearningEventLog {
  id: string;
  occurred_at: string | null;
  english_text: string;
  chinese_text: string | null;
  response_text: string | null;
  is_correct: boolean | null;
  score: number | null;
  review_mode: string | null;
  duration_ms: number;
  error_type: string | null;
}

export interface MinuteEvents {
  date: string;
  hour: number;
  minute: number;
  events: LearningEventLog[];
}

import { getApiBaseUrl } from "./api-base-url";
import { fetchWithAuth, parseApiError } from "./api";

export async function getHeatmap(accessToken: string, year?: number): Promise<Heatmap> {
  const q = year ? `?year=${year}` : "";
  const r = await fetchWithAuth(`${getApiBaseUrl()}/reports/learning/heatmap${q}`, { cache: "no-store" }, accessToken);
  if (!r.ok) throw new Error(await parseApiError(r));
  return (await r.json()) as Heatmap;
}

export async function getDayDetail(accessToken: string, date: string): Promise<DayDetail> {
  const r = await fetchWithAuth(`${getApiBaseUrl()}/reports/learning/day/${date}`, { cache: "no-store" }, accessToken);
  if (!r.ok) throw new Error(await parseApiError(r));
  return (await r.json()) as DayDetail;
}

export async function getHourDetail(accessToken: string, date: string, hour: number): Promise<{ date: string; hour: number; minutes: MinuteBreakdown[]; minute_modes: MinuteModeRow[] }> {
  const r = await fetchWithAuth(`${getApiBaseUrl()}/reports/learning/hour/${date}/${hour}`, { cache: "no-store" }, accessToken);
  if (!r.ok) throw new Error(await parseApiError(r));
  return (await r.json()) as { date: string; hour: number; minutes: MinuteBreakdown[]; minute_modes: MinuteModeRow[] };
}

export async function getMinuteEvents(accessToken: string, date: string, hour: number, minute: number): Promise<MinuteEvents> {
  const r = await fetchWithAuth(`${getApiBaseUrl()}/reports/learning/minute/${date}/${minute}?hour=${hour}`, { cache: "no-store" }, accessToken);
  if (!r.ok) throw new Error(await parseApiError(r));
  return (await r.json()) as MinuteEvents;
}

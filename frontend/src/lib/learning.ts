import { fetchWithAuth, getFreshAccessToken, parseApiError } from "@/lib/api";
import { getApiBaseUrl } from "@/lib/api-base-url";
import { ModelSettings } from "@/lib/model-settings";

export interface LearningItem {
  id: string;
  user_id: string;
  item_type: "word" | "phrase" | "sentence";
  english_text: string;
  chinese_text: string;
  phonetic: string | null;
  syllables: string[] | null;
  grapheme_phoneme_map: Record<string, string> | null;
  difficulty_level: number;
  sort_order: number;
  unit_label: string | null;
  source: string | null;
  source_item_id?: string;
  review_task_id?: string;
  review_task_type?: string;
  review_prompt?: string | null;
  review_choices?: string[];
  review_answer?: string | null;
  focus_words?: string[];
  course_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ImportSkippedItem {
  english_text: string;
  reason: string;
}

export interface LearningImportResponse {
  imported_count: number;
  skipped_count: number;
  total_rows: number;
  items: LearningItem[];
  skipped_items: ImportSkippedItem[];
}

export interface LearningTranslationResponse {
  english_text: string;
  chinese_text: string;
}

export interface WordTranslationsResponse {
  translations: Record<string, string>;
}

export interface DynamicSentenceResponse {
  english_text: string;
  chinese_text: string;
  focus_words: string[];
  known_words: string[];
  weak_words: string[];
}

export interface LearningEncouragementResponse {
  chinese_text: string;
  english_text: string;
}

export interface LearningImportProgress {
  percent: number;
  message: string;
}

export interface CourseCacheRebuildStats {
  items: number;
  sentence_translations: number;
  term_translations: number;
  speech_cached: number;
  speech_missing: number;
  errors: number;
}

export interface CourseCacheRebuildProgress {
  status: "running" | "done" | "error";
  percent: number;
  message: string;
  stats: CourseCacheRebuildStats;
}

export interface CourseCacheStatusSummary {
  total_items: number;
  sentence_translations_ready: number;
  sentence_english_audio_ready: number;
  sentence_chinese_audio_ready: number;
  total_terms: number;
  term_translations_ready: number;
  word_english_audio_ready: number;
  word_chinese_audio_ready: number;
  speech_assets_ready: number;
  total_speech_assets: number;
}

export interface CourseCacheItemStatus {
  learning_item_id: string;
  sentence_chinese_translation_ready: boolean;
  sentence_english_audio_ready: boolean;
  sentence_chinese_audio_ready: boolean;
  word_translations_ready: boolean;
  word_english_audio_ready: boolean;
  word_chinese_audio_ready: boolean;
}

export interface CourseCacheStatus {
  course_id: string;
  summary: CourseCacheStatusSummary;
  items: CourseCacheItemStatus[];
}

export interface UploadLearningItemsOptions {
  onProgress?: (progress: LearningImportProgress) => void;
  timeoutMs?: number;
}

export function validateImportFile(file: File | null): string | null {
  if (!file) {
    return "请选择 TXT 或 Excel 文件";
  }

  const lowerName = file.name.toLowerCase();
  if (!lowerName.endsWith(".txt") && !lowerName.endsWith(".xlsx")) {
    return "仅支持 .txt 和 .xlsx 文件";
  }

  if (file.size === 0) {
    return "文件不能为空";
  }

  if (file.size > 5 * 1024 * 1024) {
    return "文件不能超过 5MB";
  }

  return null;
}

export async function uploadLearningItems(
  file: File,
  courseId: string,
  accessToken: string,
  modelSettings: ModelSettings,
  options: UploadLearningItemsOptions = {},
): Promise<LearningImportResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("course_id", courseId);
  formData.append("llm_provider", modelSettings.llmProvider);
  formData.append("llm_base_url", modelSettings.llmBaseUrl);
  formData.append("llm_model", modelSettings.llmModel);
  formData.append("llm_api_key", modelSettings.llmApiKey);

  options.onProgress?.({ percent: 8, message: "准备上传文件..." });

  const response = await uploadFormDataWithAuth(`${getApiBaseUrl()}/learning/imports`, formData, accessToken, options);
  return response;
}

async function uploadFormDataWithAuth(
  url: string,
  formData: FormData,
  accessToken: string,
  options: UploadLearningItemsOptions,
): Promise<LearningImportResponse> {
  let response = await uploadFormData(url, formData, accessToken, options);
  if (response.status !== 401) {
    return parseUploadResponse(response);
  }

  const refreshedAccessToken = await getFreshAccessToken();
  if (!refreshedAccessToken) {
    return parseUploadResponse(response);
  }

  options.onProgress?.({ percent: 8, message: "登录状态已刷新，重新上传文件..." });
  response = await uploadFormData(url, formData, refreshedAccessToken, options);
  return parseUploadResponse(response);
}

async function parseUploadResponse(response: Response): Promise<LearningImportResponse> {
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return (await response.json()) as LearningImportResponse;
}

function uploadFormData(
  url: string,
  formData: FormData,
  accessToken: string,
  options: UploadLearningItemsOptions,
): Promise<Response> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.timeout = options.timeoutMs ?? 5 * 60 * 1000;
    request.setRequestHeader("Authorization", `Bearer ${accessToken}`);
    request.responseType = "text";

    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        options.onProgress?.({ percent: 25, message: "正在上传文件..." });
        return;
      }

      const uploadPercent = Math.round((event.loaded / event.total) * 60);
      options.onProgress?.({ percent: Math.min(70, 10 + uploadPercent), message: "正在上传文件..." });
    };

    request.upload.onload = () => {
      options.onProgress?.({ percent: 78, message: "文件已上传，正在解析并导入..." });
    };

    request.onload = () => {
      options.onProgress?.({ percent: 90, message: "导入完成，正在整理结果..." });
      resolve(
        new Response(request.responseText, {
          status: request.status,
          statusText: request.statusText,
          headers: parseXhrHeaders(request.getAllResponseHeaders()),
        }),
      );
    };

    request.onerror = () => {
      reject(new Error("上传失败：网络连接异常"));
    };

    request.ontimeout = () => {
      reject(new Error("上传超时：请检查本地模型或网络模型是否可用，或先给文件补充中文释义后再导入"));
    };

    request.send(formData);
  });
}

function parseXhrHeaders(rawHeaders: string): Headers {
  const headers = new Headers();
  rawHeaders
    .trim()
    .split(/[\r\n]+/)
    .filter(Boolean)
    .forEach((line) => {
      const separatorIndex = line.indexOf(":");
      if (separatorIndex <= 0) {
        return;
      }
      headers.append(line.slice(0, separatorIndex).trim(), line.slice(separatorIndex + 1).trim());
    });
  return headers;
}

export async function translateLearningText(
  englishText: string,
  accessToken: string,
  modelSettings: ModelSettings,
): Promise<LearningTranslationResponse> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/translations`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        english_text: englishText,
        llm_provider: modelSettings.llmProvider,
        llm_base_url: modelSettings.llmBaseUrl,
        llm_model: modelSettings.llmModel,
        llm_api_key: modelSettings.llmApiKey,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return (await response.json()) as LearningTranslationResponse;
}

export async function getWordTranslations(
  words: string[],
  accessToken: string,
  modelSettings: ModelSettings,
  courseId?: string | null,
): Promise<Record<string, string>> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/word-translations`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        words,
        course_id: courseId || undefined,
        llm_provider: modelSettings.llmProvider,
        llm_base_url: modelSettings.llmBaseUrl,
        llm_model: modelSettings.llmModel,
        llm_api_key: modelSettings.llmApiKey,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  const payload = (await response.json()) as WordTranslationsResponse;
  return payload.translations;
}

export async function rebuildCourseCache(
  courseId: string,
  accessToken: string,
  modelSettings: ModelSettings,
  onProgress: (progress: CourseCacheRebuildProgress) => void,
): Promise<CourseCacheRebuildProgress> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/courses/${courseId}/cache-rebuild`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        llm_provider: modelSettings.llmProvider,
        llm_base_url: modelSettings.llmBaseUrl,
        llm_model: modelSettings.llmModel,
        llm_api_key: modelSettings.llmApiKey,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  if (!response.body) {
    throw new Error("缓存重建没有返回进度流");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastProgress: CourseCacheRebuildProgress | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const progress = parseCourseCacheProgressLine(line);
      if (!progress) {
        continue;
      }
      lastProgress = progress;
      onProgress(progress);
      if (progress.status === "error") {
        throw new Error(progress.message);
      }
    }
  }

  if (buffer.trim()) {
    const progress = parseCourseCacheProgressLine(buffer);
    if (progress) {
      lastProgress = progress;
      onProgress(progress);
    }
  }

  if (!lastProgress) {
    throw new Error("缓存重建没有返回有效进度");
  }
  return lastProgress;
}

export async function retryItemCache(
  courseId: string,
  itemId: string,
  accessToken: string,
  modelSettings: ModelSettings,
): Promise<CourseCacheStatus> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/courses/${courseId}/cache-retry/${itemId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        llm_provider: modelSettings.llmProvider,
        llm_base_url: modelSettings.llmBaseUrl,
        llm_model: modelSettings.llmModel,
        llm_api_key: modelSettings.llmApiKey,
      }),
      cache: "no-store",
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as CourseCacheStatus;
}

export async function getCourseCacheStatus(courseId: string, accessToken: string): Promise<CourseCacheStatus> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/courses/${courseId}/cache-status`,
    {
      cache: "no-store",
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return (await response.json()) as CourseCacheStatus;
}

function parseCourseCacheProgressLine(line: string): CourseCacheRebuildProgress | null {
  const trimmed = line.trim();
  if (!trimmed) {
    return null;
  }
  return JSON.parse(trimmed) as CourseCacheRebuildProgress;
}

export async function logWordMistake(
  learningItemId: string,
  expectedWord: string,
  actualWord: string,
  accessToken: string,
  errorType = "spelling",
): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/word-mistakes`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        learning_item_id: learningItemId,
        expected_word: expectedWord,
        actual_word: actualWord,
        error_type: errorType,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function logWordReview(
  payload: {
    learning_item_id: string;
    review_task_id?: string;
    word: string;
    score: number;
    review_mode: string;
    response_text?: string;
    duration_seconds?: number;
    error_type?: string;
    encoding_stage?: string;
    encoding_duration_ms?: number;
  },
  accessToken: string,
): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/word-reviews`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...payload,
        duration_seconds: payload.duration_seconds ?? 0,
        encoding_duration_ms: payload.encoding_duration_ms ?? 0,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function generateDynamicSentence(
  payload: {
    course_id: string | null;
    current_sentence: string;
    mistaken_words: string[];
  },
  accessToken: string,
  modelSettings: ModelSettings,
): Promise<DynamicSentenceResponse> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/dynamic-sentences`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...payload,
        llm_provider: modelSettings.llmProvider,
        llm_base_url: modelSettings.llmBaseUrl,
        llm_model: modelSettings.llmModel,
        llm_api_key: modelSettings.llmApiKey,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return (await response.json()) as DynamicSentenceResponse;
}

export async function generateLearningEncouragement(
  payload: {
    course_name: string;
    duration_seconds: number;
  },
  accessToken: string,
  modelSettings: ModelSettings,
): Promise<LearningEncouragementResponse> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/encouragements`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...payload,
        llm_provider: modelSettings.llmProvider,
        llm_base_url: modelSettings.llmBaseUrl,
        llm_model: modelSettings.llmModel,
        llm_api_key: modelSettings.llmApiKey,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return (await response.json()) as LearningEncouragementResponse;
}

export async function listLearningItems(accessToken: string, courseId?: string, limit?: number): Promise<LearningItem[]> {
  const params = new URLSearchParams();
  if (courseId) {
    params.set("course_id", courseId);
  }
  if (limit && limit > 0) {
    params.set("limit", String(limit));
  }
  const queryString = params.toString() ? `?${params.toString()}` : "";
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/items${queryString}`,
    {
      cache: "no-store",
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return (await response.json()) as LearningItem[];
}

export async function listDueReviewItems(
  accessToken: string,
  excludeCourseId?: string,
  limit = 12,
  interleave = false,
  reviewCap?: number,
  focus = false,
  phonics = false,
): Promise<LearningItem[]> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (excludeCourseId) {
    params.set("exclude_course_id", excludeCourseId);
  }
  if (interleave) {
    params.set("interleave", "true");
  }
  if (focus) {
    params.set("focus", "true");
  }
  if (phonics) {
    params.set("phonics", "true");
  }
  if (reviewCap !== undefined) {
    params.set("review_cap", String(reviewCap));
  }

  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/learning/review-items?${params.toString()}`,
    {
      cache: "no-store",
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return (await response.json()) as LearningItem[];
}

import { refreshAccessToken } from "@/lib/auth";
import { ModelSettings } from "@/lib/model-settings";

export interface LearningItem {
  id: string;
  user_id: string;
  item_type: "word" | "phrase" | "sentence";
  english_text: string;
  chinese_text: string;
  phonetic: string | null;
  difficulty_level: number;
  source: string | null;
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

export interface ApiErrorResponse {
  detail?: string;
}

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
let refreshTokenPromise: Promise<string | null> | null = null;

async function getFreshAccessToken(): Promise<string | null> {
  refreshTokenPromise ??= refreshAccessToken().finally(() => {
    refreshTokenPromise = null;
  });
  return refreshTokenPromise;
}

async function parseUploadError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorResponse;
    return body.detail ?? `上传失败：${response.status}`;
  } catch {
    return `上传失败：${response.status}`;
  }
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

export async function uploadLearningItems(
  file: File,
  courseId: string,
  accessToken: string,
  modelSettings: ModelSettings,
): Promise<LearningImportResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("course_id", courseId);
  formData.append("llm_base_url", modelSettings.llmBaseUrl);
  formData.append("llm_model", modelSettings.llmModel);

  const response = await fetchWithAuth(
    `${apiBaseUrl}/learning/imports`,
    {
      method: "POST",
      body: formData,
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseUploadError(response));
  }

  return (await response.json()) as LearningImportResponse;
}

export async function translateLearningText(
  englishText: string,
  accessToken: string,
  modelSettings: ModelSettings,
): Promise<LearningTranslationResponse> {
  const response = await fetchWithAuth(
    `${apiBaseUrl}/learning/translations`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        english_text: englishText,
        llm_base_url: modelSettings.llmBaseUrl,
        llm_model: modelSettings.llmModel,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseUploadError(response));
  }

  return (await response.json()) as LearningTranslationResponse;
}

export async function listLearningItems(accessToken: string, courseId?: string): Promise<LearningItem[]> {
  const queryString = courseId ? `?course_id=${courseId}` : "";
  const response = await fetchWithAuth(
    `${apiBaseUrl}/learning/items${queryString}`,
    {
      cache: "no-store",
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseUploadError(response));
  }

  return (await response.json()) as LearningItem[];
}

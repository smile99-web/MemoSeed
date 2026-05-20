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

export interface DynamicSentenceResponse {
  english_text: string;
  chinese_text: string;
  focus_words: string[];
  known_words: string[];
  weak_words: string[];
}

export interface ApiErrorResponse {
  detail?: string;
}

export interface LearningImportProgress {
  percent: number;
  message: string;
}

export interface UploadLearningItemsOptions {
  onProgress?: (progress: LearningImportProgress) => void;
  timeoutMs?: number;
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

  const response = await uploadFormDataWithAuth(`${apiBaseUrl}/learning/imports`, formData, accessToken, options);
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
    throw new Error(await parseUploadError(response));
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
    `${apiBaseUrl}/learning/translations`,
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
    throw new Error(await parseUploadError(response));
  }

  return (await response.json()) as LearningTranslationResponse;
}

export async function logWordMistake(
  learningItemId: string,
  expectedWord: string,
  actualWord: string,
  accessToken: string,
): Promise<void> {
  const response = await fetchWithAuth(
    `${apiBaseUrl}/learning/word-mistakes`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        learning_item_id: learningItemId,
        expected_word: expectedWord,
        actual_word: actualWord,
      }),
    },
    accessToken,
  );

  if (!response.ok) {
    throw new Error(await parseUploadError(response));
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
    `${apiBaseUrl}/learning/dynamic-sentences`,
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
    throw new Error(await parseUploadError(response));
  }

  return (await response.json()) as DynamicSentenceResponse;
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

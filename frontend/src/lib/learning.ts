export interface LearningItem {
  id: string;
  user_id: string;
  item_type: "word" | "phrase" | "sentence";
  english_text: string;
  chinese_text: string;
  phonetic: string | null;
  difficulty_level: number;
  source: string | null;
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

export interface ApiErrorResponse {
  detail?: string;
}

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

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

export async function uploadLearningItems(file: File, accessToken: string): Promise<LearningImportResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${apiBaseUrl}/learning/imports`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
    body: formData,
  });

  if (!response.ok) {
    throw new Error(await parseUploadError(response));
  }

  return (await response.json()) as LearningImportResponse;
}

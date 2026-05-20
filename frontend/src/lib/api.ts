import { getApiBaseUrl } from "@/lib/api-base-url";

export interface HealthResponse {
  status: string;
  service: string;
}

export interface ApiErrorResponse {
  detail?: string;
}

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function parseApiError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorResponse;
    return body.detail ?? `API request failed: ${response.status}`;
  } catch {
    return `API request failed: ${response.status}`;
  }
}

export async function apiRequest<TResponse, TBody extends object | undefined = undefined>(
  path: string,
  options: {
    method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
    body?: TBody;
    accessToken?: string;
  } = {},
): Promise<TResponse> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (options.accessToken) {
    headers.Authorization = `Bearer ${options.accessToken}`;
  }

  const apiBaseUrl = getApiBaseUrl();
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error(`无法连接后端服务（${apiBaseUrl}）。请确认后端已启动，并检查 NEXT_PUBLIC_API_BASE_URL 与 CORS 配置。`);
    }
    throw error;
  }
  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response), response.status);
  }

  return (await response.json()) as TResponse;
}

export async function apiGet<TResponse>(path: string): Promise<TResponse> {
  return apiRequest<TResponse>(path);
}

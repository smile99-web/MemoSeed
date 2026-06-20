import { getApiBaseUrl } from "@/lib/api-base-url";
import { refreshAccessToken } from "@/lib/auth";

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

export async function parseApiError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorResponse;
    return body.detail ?? `API request failed: ${response.status}`;
  } catch {
    return `API request failed: ${response.status}`;
  }
}

export async function getFreshAccessToken(): Promise<string | null> {
  return refreshAccessToken();
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init: RequestInit,
  accessToken: string,
): Promise<Response> {
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

export async function apiRequest<TResponse, TBody extends object | undefined = undefined>(
  path: string,
  options: {
    method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
    body?: TBody;
    accessToken?: string;
  } = {},
): Promise<TResponse> {
  const apiBaseUrl = getApiBaseUrl();

  const buildHeaders = (token?: string): Record<string, string> => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (token) {
      h.Authorization = `Bearer ${token}`;
    }
    return h;
  };

  const executeFetch = async (token: string | undefined): Promise<Response> => {
    try {
      return await fetch(`${apiBaseUrl}${path}`, {
        method: options.method ?? "GET",
        headers: buildHeaders(token ?? options.accessToken),
        body: options.body ? JSON.stringify(options.body) : undefined,
        cache: "no-store",
      });
    } catch (error) {
      if (error instanceof TypeError) {
        throw new Error(`无法连接后端服务（${apiBaseUrl}）。请确认后端已启动，并检查 NEXT_PUBLIC_API_BASE_URL 与 CORS 配置。`);
      }
      throw error;
    }
  };

  // First attempt: use the caller's token (or no token for unauthenticated
  // endpoints like /auth/login). On 401, try to refresh the access token
  // via the refresh-token cookie and retry once. If the refresh fails or
  // the retry still returns 401, surface the original 401 error.
  //
  // Without this, callers that go through apiRequest (export/import,
  // reports) fail on token expiry until the user manually re-logs in.
  let response = await executeFetch(options.accessToken);
  if (response.status === 401 && !options.accessToken) {
    // No caller-supplied token but we got 401 — try a refresh in case a
    // refresh-cookie is still valid.
    const refreshed = await getFreshAccessToken();
    if (refreshed) {
      response = await executeFetch(refreshed);
    }
  } else if (response.status === 401 && options.accessToken) {
    const refreshed = await getFreshAccessToken();
    if (refreshed && refreshed !== options.accessToken) {
      response = await executeFetch(refreshed);
    }
  }

  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response), response.status);
  }

  return (await response.json()) as TResponse;
}

export async function apiGet<TResponse>(path: string): Promise<TResponse> {
  return apiRequest<TResponse>(path);
}

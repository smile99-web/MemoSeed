const defaultApiBaseUrl = "http://127.0.0.1:8000/api/v1";

export function getApiBaseUrl(): string {
  const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? defaultApiBaseUrl;
  if (typeof window === "undefined") {
    return configuredApiBaseUrl.replace(/\/$/, "");
  }

  try {
    const apiUrl = new URL(configuredApiBaseUrl);
    if (isLoopbackHost(apiUrl.hostname) && isLoopbackHost(window.location.hostname)) {
      return "/api/v1";
    }
    return apiUrl.toString().replace(/\/$/, "");
  } catch {
    return configuredApiBaseUrl.replace(/\/$/, "");
  }
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

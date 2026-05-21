const defaultApiBaseUrl = "/api/v1";

export function getApiBaseUrl(): string {
  const configuredApiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? defaultApiBaseUrl).replace(/\/$/, "");
  if (configuredApiBaseUrl.startsWith("/")) {
    return configuredApiBaseUrl;
  }

  if (typeof window === "undefined") {
    return configuredApiBaseUrl;
  }

  try {
    const apiUrl = new URL(configuredApiBaseUrl);
    if (isLoopbackHost(apiUrl.hostname)) {
      return "/api/v1";
    }
    return apiUrl.toString().replace(/\/$/, "");
  } catch {
    return configuredApiBaseUrl;
  }
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

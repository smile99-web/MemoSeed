import { apiRequest } from "@/lib/api";
import { getApiBaseUrl } from "@/lib/api-base-url";

export interface AuthUser {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
}

export interface AuthResponse {
  user: AuthUser;
  tokens: TokenPair;
}

export interface RegisterPayload {
  email: string;
  username: string;
  password: string;
  // Optional — only checked when the server configures INVITE_CODE.
  invite_code?: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

const accessTokenKey = "memoseed_access_token";
const refreshTokenKey = "memoseed_refresh_token";
const userKey = "memoseed_user";
let refreshTokenPromise: Promise<string | null> | null = null;

export function validateEmail(email: string): string | null {
  if (!email.trim()) {
    return "请输入邮箱";
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return "请输入有效邮箱";
  }
  return null;
}

export function validatePassword(password: string): string | null {
  if (password.length < 8) {
    return "密码至少 8 位";
  }
  if (password.length > 128) {
    return "密码不能超过 128 位";
  }
  return null;
}

export function validateUsername(username: string): string | null {
  const trimmedUsername = username.trim();
  if (trimmedUsername.length < 2) {
    return "用户名至少 2 个字符";
  }
  if (trimmedUsername.length > 80) {
    return "用户名不能超过 80 个字符";
  }
  return null;
}

export function saveAuthSession(auth: AuthResponse): void {
  window.localStorage.setItem(accessTokenKey, auth.tokens.access_token);
  window.localStorage.setItem(refreshTokenKey, auth.tokens.refresh_token);
  window.localStorage.setItem(userKey, JSON.stringify(auth.user));
}

export function getAccessToken(): string | null {
  return window.localStorage.getItem(accessTokenKey);
}

export function getAuthUser(): AuthUser | null {
  const storedUser = window.localStorage.getItem(userKey);
  if (!storedUser) {
    return null;
  }

  try {
    return JSON.parse(storedUser) as AuthUser;
  } catch {
    // User blob is corrupt (truncated write, schema mismatch, etc).
    // Clear ONLY the user key — leave the access/refresh tokens alone
    // since they may still be valid. Previously we called
    // clearAuthSession() here which nuked everything and forced a
    // re-login even though the user's session was technically still
    // usable. Now we just clear the broken user metadata; the next
    // /users/me call will repopulate it.
    window.localStorage.removeItem(userKey);
    return null;
  }
}

export function isAuthenticated(): boolean {
  return Boolean(getAccessToken() && getAuthUser());
}

export function clearAuthSession(): void {
  window.localStorage.removeItem(accessTokenKey);
  window.localStorage.removeItem(refreshTokenKey);
  window.localStorage.removeItem(userKey);
}

export async function register(payload: RegisterPayload): Promise<AuthResponse> {
  return apiRequest<AuthResponse, RegisterPayload>("/auth/register", {
    method: "POST",
    body: payload,
    // 注册失败的 401 是业务错误，不能触发 refresh 旋转/清会话。
    skipAuthRefresh: true,
  });
}

export function getRefreshToken(): string | null {
  return window.localStorage.getItem(refreshTokenKey);
}

export async function login(payload: LoginPayload): Promise<AuthResponse> {
  return apiRequest<AuthResponse, LoginPayload>("/auth/login", {
    method: "POST",
    body: payload,
    // 密码错误等 401 是业务错误，不能触发 refresh 旋转/清会话。
    skipAuthRefresh: true,
  });
}

export async function refreshAccessToken(): Promise<string | null> {
  refreshTokenPromise ??= refreshAccessTokenOnce().finally(() => {
    refreshTokenPromise = null;
  });
  return refreshTokenPromise;
}

async function refreshAccessTokenOnce(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return null;
  }

  try {
    // Bare fetch — NOT apiRequest. On a 401 apiRequest would call
    // getFreshAccessToken() → refreshAccessToken(), which returns THIS very
    // in-flight refresh promise: a circular await that never settles. With an
    // expired refresh token (e.g. iPad untouched for 90+ days) every authed
    // request hung forever and the app sat on "正在加载" until a manual
    // page reload (2026-08-09 fix).
    const response = await fetch(`${getApiBaseUrl()}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`Refresh failed: ${response.status}`);
    }
    const tokens = (await response.json()) as TokenPair;
    window.localStorage.setItem(accessTokenKey, tokens.access_token);
    window.localStorage.setItem(refreshTokenKey, tokens.refresh_token);
    return tokens.access_token;
  } catch {
    // The refresh itself failed. Two real reasons:
    //   (a) the refresh token is genuinely dead (revoked server-side, or
    //       expired) — we must log the user out;
    //   (b) transient network blip — another device on the same account may
    //       have just rotated the token and our in-flight call raced.
    // Before nuking the session, give the user a chance: try one more time
    // with whatever refresh token is currently in storage (a sibling tab
    // or another device may have already replaced it). This stops the
    // "我刚登录就被另一台设备挤掉" symptom on flaky networks.
    if (getRefreshToken() && getRefreshToken() !== refreshToken) {
      return getAccessToken();
    }
    // Last attempt: re-read the refresh token one more time after a tiny
    // delay. If it changed (e.g., a parallel tab's refresh succeeded), use
    // the new access token and skip the logout.
    await new Promise<void>((resolve) => window.setTimeout(resolve, 50));
    if (getRefreshToken() && getRefreshToken() !== refreshToken) {
      return getAccessToken();
    }
    clearAuthSession();
    notifySessionExpired();
    return null;
  }
}

const SESSION_EXPIRED_EVENT = "memoseed:session-expired";

export function onSessionExpired(listener: () => void): () => void {
  if (typeof window === "undefined") {
    return () => undefined;
  }
  window.addEventListener(SESSION_EXPIRED_EVENT, listener);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
}

function notifySessionExpired(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

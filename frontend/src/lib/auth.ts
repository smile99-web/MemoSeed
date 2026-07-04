import { apiRequest } from "@/lib/api";

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
  });
}

export function getRefreshToken(): string | null {
  return window.localStorage.getItem(refreshTokenKey);
}

export async function login(payload: LoginPayload): Promise<AuthResponse> {
  return apiRequest<AuthResponse, LoginPayload>("/auth/login", {
    method: "POST",
    body: payload,
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
    clearAuthSession();
    return null;
  }

  try {
    const tokens = await apiRequest<TokenPair, { refresh_token: string }>("/auth/refresh", {
      method: "POST",
      body: { refresh_token: refreshToken },
    });
    window.localStorage.setItem(accessTokenKey, tokens.access_token);
    window.localStorage.setItem(refreshTokenKey, tokens.refresh_token);
    return tokens.access_token;
  } catch {
    if (getRefreshToken() !== refreshToken) {
      return getAccessToken();
    }
    clearAuthSession();
    return null;
  }
}

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

export async function login(payload: LoginPayload): Promise<AuthResponse> {
  return apiRequest<AuthResponse, LoginPayload>("/auth/login", {
    method: "POST",
    body: payload,
  });
}

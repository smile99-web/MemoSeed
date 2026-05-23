import { refreshAccessToken } from "@/lib/auth";
import { getApiBaseUrl } from "@/lib/api-base-url";

export interface CoursePackage {
  id: string;
  user_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface Course {
  id: string;
  user_id: string;
  package_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface CoursePackagePayload {
  name: string;
  description: string;
}

export interface CoursePayload {
  package_id: string;
  name: string;
  description: string;
}

export interface PackageExportItem {
  item_type: string;
  english_text: string;
  chinese_text: string;
  phonetic: string | null;
  difficulty_level: number;
}

export interface PackageExportCourse {
  name: string;
  description: string;
  items: PackageExportItem[];
}

export interface PackageExportData {
  version: number;
  package: CoursePackagePayload;
  courses: PackageExportCourse[];
}

export interface PackageImportResult {
  imported_package_name: string;
  courses_count: number;
  items_count: number;
}

interface ApiErrorResponse {
  detail?: string;
}

let refreshTokenPromise: Promise<string | null> | null = null;

async function getFreshAccessToken(): Promise<string | null> {
  refreshTokenPromise ??= refreshAccessToken().finally(() => {
    refreshTokenPromise = null;
  });
  return refreshTokenPromise;
}

async function parseApiError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorResponse;
    return body.detail ?? `请求失败：${response.status}`;
  } catch {
    return `请求失败：${response.status}`;
  }
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

export async function listCoursePackages(accessToken: string): Promise<CoursePackage[]> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/courses/packages`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as CoursePackage[];
}

export async function createCoursePackage(accessToken: string, payload: CoursePackagePayload): Promise<CoursePackage> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/courses/packages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as CoursePackage;
}

export async function deleteCoursePackage(accessToken: string, packageId: string): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/courses/packages/${packageId}`,
    {
      method: "DELETE",
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function listCourses(accessToken: string, packageId: string): Promise<Course[]> {
  const response = await fetchWithAuth(`${getApiBaseUrl()}/courses/courses?package_id=${packageId}`, { cache: "no-store" }, accessToken);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as Course[];
}

export async function deleteCourse(accessToken: string, courseId: string): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/courses/courses/${courseId}`,
    {
      method: "DELETE",
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
}

export async function createCourse(accessToken: string, payload: CoursePayload): Promise<Course> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/courses/courses`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as Course;
}

export async function exportCoursePackage(accessToken: string, packageId: string, packageName: string): Promise<void> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/courses/packages/${packageId}/export`,
    {},
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  const data = (await response.json()) as PackageExportData;
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${packageName.replace(/[/\\?%*:|"<> ]/g, "_")}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export async function importCoursePackage(accessToken: string, jsonData: PackageExportData): Promise<PackageImportResult> {
  const response = await fetchWithAuth(
    `${getApiBaseUrl()}/courses/packages/import`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(jsonData),
    },
    accessToken,
  );
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  return (await response.json()) as PackageImportResult;
}

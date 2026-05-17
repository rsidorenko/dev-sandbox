/** Shared API client — httponly cookie auth, JSON transport. */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; detail?: string };

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<ApiResult<T>> {
  try {
    const headers: Record<string, string> = {
      ...(options?.headers as Record<string, string>),
    };
    if (options?.body) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      credentials: "include",
    });
    const body = await res.json();
    if (res.ok && body.ok) {
      return { ok: true, data: body as T };
    }
    return {
      ok: false,
      error: body.error || "unknown_error",
      detail: body.detail,
    };
  } catch {
    return { ok: false, error: "network_error" };
  }
}

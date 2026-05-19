/** Shared API client — httponly cookie auth, JSON transport, CSRF protection. */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; detail?: string };

/** Read a cookie value by name. */
function getCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`));
  return match?.split("=")[1];
}

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
    // Attach CSRF token for non-GET requests
    if (options?.method && options.method !== "GET") {
      const csrf = getCookie("csrf_token");
      if (csrf) {
        headers["X-CSRF-Token"] = csrf;
      }
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

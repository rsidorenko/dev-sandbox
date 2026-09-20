/**
 * Общий API-клиент — Bearer-токен подписки из памяти (link-auth), JSON-транспорт.
 *
 * Пользовательская сессия — только `Authorization: Bearer <subscription_token>`;
 * куки не отправляем и не ставим (уход от cookies как ПД). CSRF-заголовок не
 * нужен: кастомный заголовок нельзя отправить кросс-сайтово без CORS-preflight.
 * Админка использует отдельный adminFetch (admin_session + admin_csrf_token).
 */

import { getToken } from "@/shared/lib/tokenStore";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
    const token = getToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
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

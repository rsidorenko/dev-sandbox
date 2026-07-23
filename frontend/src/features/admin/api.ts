/** Админ-API клиент. Auth — через httpOnly-куку admin_session (credentials: include),
 * CSRF — через отдельную admin_csrf_token (не пользовательскую csrf_token). */

import type {
  AdminAddDaysResponse,
  AdminPromocode,
  AdminPromocodePayload,
  AdminPromocodesResponse,
  AdminUsersResponse,
} from "@/entities/admin/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type AdminApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; detail?: string };

function getCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie.split("; ").find((row) => row.startsWith(`${name}=`));
  return match?.split("=")[1];
}

export async function adminFetch<T>(path: string, options?: RequestInit): Promise<AdminApiResult<T>> {
  try {
    const headers: Record<string, string> = {
      ...(options?.headers as Record<string, string>),
    };
    if (options?.body) {
      headers["Content-Type"] = "application/json";
    }
    if (options?.method && options.method !== "GET") {
      const csrf = getCookie("admin_csrf_token");
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
    return { ok: false, error: body.error || "unknown_error", detail: body.detail };
  } catch {
    return { ok: false, error: "network_error" };
  }
}

export const adminApi = {
  login(username: string, password: string) {
    return adminFetch<{ csrf_token: string; username: string }>(
      "/api/v1/admin/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
    );
  },
  logout() {
    return adminFetch<{ ok: true }>("/api/v1/admin/auth/logout", { method: "POST" });
  },
  listUsers(params: { limit?: number; offset?: number; search?: string } = {}) {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    if (params.search) q.set("search", params.search);
    const qs = q.toString();
    return adminFetch<AdminUsersResponse>(`/api/v1/admin/users${qs ? `?${qs}` : ""}`);
  },
  listPromocodes() {
    return adminFetch<AdminPromocodesResponse>("/api/v1/admin/promocodes");
  },
  createPromocode(payload: AdminPromocodePayload) {
    return adminFetch<{ promocode: AdminPromocode }>("/api/v1/admin/promocodes", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updatePromocode(code: string, changes: Partial<AdminPromocodePayload>) {
    return adminFetch<{ promocode: AdminPromocode }>(`/api/v1/admin/promocodes/${encodeURIComponent(code)}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    });
  },
  deletePromocode(code: string) {
    return adminFetch<{ ok: true }>(`/api/v1/admin/promocodes/${encodeURIComponent(code)}`, {
      method: "DELETE",
    });
  },
  addDays(internal_user_id: string, days: number) {
    return adminFetch<AdminAddDaysResponse>(
      `/api/v1/admin/users/${encodeURIComponent(internal_user_id)}/add-days`,
      { method: "POST", body: JSON.stringify({ days }) },
    );
  },
};

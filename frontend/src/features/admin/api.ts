/** Админ-API клиент. Auth — через httpOnly-куку admin_session (credentials: include),
 * CSRF — через отдельную admin_csrf_token (не пользовательскую csrf_token). */

import type {
  AdminAddDaysResponse,
  AdminUserDetail,
  AdminGlobalDiscount,
  AdminGlobalDiscountPayload,
  AdminPlanPrice,
  AdminPlansResponse,
  AdminPromocode,
  AdminPromocodePayload,
  AdminPromocodesResponse,
  AdminStatsPeriod,
  AdminStatsResponse,
  AdminTestUserResetResponse,
  AdminTelegramAdsSpend,
  AdminUsersResponse,
  BroadcastBonusConfig,
  BroadcastButtonsPayload,
  BroadcastEstimate,
  BroadcastJobsResponse,
  BroadcastSendResponse,
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
  getUser(id: string) {
    return adminFetch<AdminUserDetail>(`/api/v1/admin/users/${encodeURIComponent(id)}`);
  },
  listUsers(params: Record<string, string | number> = {}) {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => { if (value !== "") q.set(key, String(value)); });
    return adminFetch<AdminUsersResponse>(`/api/v1/admin/users?${q.toString()}`);
  },
  listPromocodes() {
    return adminFetch<AdminPromocodesResponse>("/api/v1/admin/promocodes");
  },
  listPlans() {
    return adminFetch<AdminPlansResponse>("/api/v1/admin/plans");
  },
  setPlanPrice(planId: string, priceRubles: number) {
    return adminFetch<{ plan: AdminPlanPrice }>(`/api/v1/admin/plans/${encodeURIComponent(planId)}`, {
      method: "PATCH",
      body: JSON.stringify({ price_rubles: priceRubles }),
    });
  },
  resetPlanPrice(planId: string) {
    return adminFetch<{ plan_id: string; price_rubles: number }>(
      `/api/v1/admin/plans/${encodeURIComponent(planId)}`,
      { method: "DELETE" },
    );
  },
  setGlobalDiscount(payload: AdminGlobalDiscountPayload) {
    return adminFetch<{ global_discount: AdminGlobalDiscount }>("/api/v1/admin/plans/global-discount", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  stats(
    period: AdminStatsPeriod,
    tz = "Europe/Moscow",
    customRange?: { dateFrom: string; dateTo: string },
  ) {
    const q = new URLSearchParams({ period, tz });
    if (period === "custom" && customRange) {
      q.set("date_from", customRange.dateFrom);
      q.set("date_to", customRange.dateTo);
    }
    return adminFetch<AdminStatsResponse>(`/api/v1/admin/stats?${q.toString()}`);
  },
  setTelegramAdsSpend(payload: { campaign_key: string; spend_date: string; amount_rubles: string }) {
    return adminFetch<{ ok: true; spend: AdminTelegramAdsSpend }>("/api/v1/admin/telegram-ads/spend", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
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
  setInternal(internal_user_id: string, is_internal: boolean, reason: string) {
    return adminFetch<{ internal_user_id: string; is_internal: boolean }>(
      `/api/v1/admin/users/${encodeURIComponent(internal_user_id)}/internal`,
      { method: "POST", body: JSON.stringify({ is_internal, reason }) },
    );
  },
  resetTestAccount(internal_user_id: string, confirmation_telegram_user_id: number, reason: string) {
    return adminFetch<AdminTestUserResetResponse>(
      `/api/v1/admin/users/${encodeURIComponent(internal_user_id)}/reset-test-account`,
      { method: "POST", body: JSON.stringify({ confirmation_telegram_user_id, reason }) },
    );
  },
  broadcastEstimate(payload: { categories: string[]; explicit_targets?: string[] }) {
    return adminFetch<{ ok: true } & BroadcastEstimate>("/api/v1/admin/broadcast/estimate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  broadcastSend(payload: {
    categories: string[];
    explicit_targets?: string[];
    message_text: string;
    disable_web_page_preview?: boolean;
    buttons?: BroadcastButtonsPayload;
    bonus?: BroadcastBonusConfig | null;
  }) {
    return adminFetch<{ ok: true } & BroadcastSendResponse>("/api/v1/admin/broadcast/send", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  broadcastTest(payload: {
    chat_id: number;
    message_text: string;
    disable_web_page_preview?: boolean;
    buttons?: BroadcastButtonsPayload;
    bonus?: BroadcastBonusConfig | null;
  }) {
    return adminFetch<{ ok: true } & Pick<BroadcastSendResponse, "bonus_warnings">>(
      "/api/v1/admin/broadcast/test",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },
  broadcastJobStatus(jobId: string) {
    return adminFetch<{ ok: true; job: BroadcastJobsResponse["jobs"][number] }>(
      `/api/v1/admin/broadcast/jobs/${encodeURIComponent(jobId)}`,
    );
  },
  listBroadcastJobs(params: { limit?: number; offset?: number } = {}) {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    const qs = q.toString();
    return adminFetch<BroadcastJobsResponse>(`/api/v1/admin/broadcast/jobs${qs ? `?${qs}` : ""}`);
  },
};

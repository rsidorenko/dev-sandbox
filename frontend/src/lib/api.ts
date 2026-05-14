/** API client for backend communication. */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type ApiResult<T> = { ok: true; data: T } | { ok: false; error: string; detail?: string };

async function apiFetch<T>(path: string, options?: RequestInit): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
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

export interface SendCodeResponse {
  ok: boolean;
  sent: boolean;
  ttl_minutes: number;
}

export interface VerifyResponse {
  ok: boolean;
  user: { telegram_user_id: number; email: string };
}

export interface UserProfile {
  user: { telegram_user_id: number; email: string; internal_user_id: string };
  subscription: {
    state: string;
    active_until: string | null;
    plan_id: string | null;
    device_count: number | null;
  } | null;
  keys: { available: boolean; status: string } | null;
  referral: {
    code: string;
    balance_rubles: number;
    referrals_count: number;
  } | null;
}

export interface PaymentCreateResponse {
  ok: boolean;
  status: string;
  plan_id: string;
  plan_name: string;
  device_count: number;
  amount_rubles: number;
  amount_kopecks: number;
  message?: string;
  payment_url?: string;
  payment_id?: string;
}

export const api = {
  auth: {
    sendCode: (email: string) =>
      apiFetch<SendCodeResponse>("/api/v1/auth/email/send-code", {
        method: "POST",
        body: JSON.stringify({ email }),
      }),
    verifyCode: (email: string, code: string) =>
      apiFetch<VerifyResponse>("/api/v1/auth/email/verify", {
        method: "POST",
        body: JSON.stringify({ email, code }),
      }),
    logout: () =>
      apiFetch<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST" }),
  },
  user: {
    profile: () => apiFetch<UserProfile>("/api/v1/user/profile"),
  },
  payment: {
    create: (planId: string, deviceCount: number = 5) =>
      apiFetch<PaymentCreateResponse>("/api/v1/payment/create", {
        method: "POST",
        body: JSON.stringify({ plan_id: planId, device_count: deviceCount }),
      }),
  },
};

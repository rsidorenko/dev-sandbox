/** API client for backend communication. */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type ApiResult<T> = { ok: true; data: T } | { ok: false; error: string; detail?: string };

function getSessionToken(): string | undefined {
  try {
    return localStorage.getItem("session") || undefined;
  } catch {
    return undefined;
  }
}

function setSessionToken(token: string): void {
  try {
    localStorage.setItem("session", token);
  } catch {
    // ignore
  }
}

function clearSessionToken(): void {
  try {
    localStorage.removeItem("session");
  } catch {
    // ignore
  }
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<ApiResult<T>> {
  try {
    const token = getSessionToken();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options?.headers as Record<string, string>),
    };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
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

export interface SendCodeResponse {
  ok: boolean;
  sent: boolean;
  ttl_minutes: number;
}

export interface VerifyResponse {
  ok: boolean;
  token: string;
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

export interface KeyEntry {
  label: string;
  country: string;
  flag: string;
  link: string;
}

export interface KeysResponse {
  ok: boolean;
  keys: KeyEntry[];
  subscription_url: string | null;
}

export interface SubscriptionActionResponse {
  ok: boolean;
  active_until?: string;
  plan_id?: string;
  device_count?: number;
  state?: string;
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
    keys: () => apiFetch<KeysResponse>("/api/v1/user/keys"),
    reissueKeys: () => apiFetch<KeysResponse>("/api/v1/user/keys/reissue", { method: "POST" }),
    renewSubscription: () => apiFetch<SubscriptionActionResponse>("/api/v1/user/subscription/renew", { method: "POST" }),
    changePlan: (planId: string) => apiFetch<SubscriptionActionResponse>("/api/v1/user/subscription/change-plan", { method: "POST", body: JSON.stringify({ plan_id: planId }) }),
    changeDevices: (count: number) => apiFetch<SubscriptionActionResponse>("/api/v1/user/subscription/change-devices", { method: "POST", body: JSON.stringify({ device_count: count }) }),
    cancelSubscription: () => apiFetch<SubscriptionActionResponse>("/api/v1/user/subscription/cancel", { method: "POST" }),
  },
  payment: {
    create: (planId: string, deviceCount: number = 5) =>
      apiFetch<PaymentCreateResponse>("/api/v1/payment/create", {
        method: "POST",
        body: JSON.stringify({ plan_id: planId, device_count: deviceCount }),
      }),
  },
};

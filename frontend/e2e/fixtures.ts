import type { Page, Route } from "@playwright/test";

export const BASE_URL = "http://localhost:3100";

/**
 * Тестовые данные и перехват API для E2E.
 *
 * Все запросы фронта к бэкенду (/api/v1/**) перехватываются через page.route()
 * и возвращают фиксированные ответы — реальный backend не нужен, тесты
 * детерминированы. Профиль определяет состояние авторизации/подписки.
 */
export type ProfileResponse = {
  user: { telegram_user_id: number; email: string; internal_user_id: string };
  subscription: {
    state: string;
    active_until: string | null;
    remaining_days: number | null;
    plan_id: string | null;
    device_count: number | null;
    trial_available?: boolean;
  } | null;
  keys: { available: boolean; status: string } | null;
  referral: {
    code: string;
    balance_rubes: number;
    referrals_count: number;
    web_referral_link?: string;
  } | null;
};

export function makeProfile(opts: {
  trialAvailable?: boolean;
  subState?: string;
}): ProfileResponse {
  return {
    user: {
      telegram_user_id: 123456,
      email: "user@example.com",
      internal_user_id: "u-1",
    },
    subscription: {
      state: opts.subState ?? "none",
      active_until: null,
      remaining_days: null,
      plan_id: null,
      device_count: null,
      trial_available: opts.trialAvailable ?? true,
    },
    keys: null,
    referral: {
      code: "ABC123",
      balance_rubes: 0,
      referrals_count: 0,
    },
  };
}

type ProfileKind = "auth" | "unauth" | "trial_used" | "active_sub";
type PaymentKind = "payment_url" | "payment_unavailable" | "unauthorized";
type SendCodeKind = "ok" | "disposable" | "rate_limited";
type VerifyKind = "ok" | "invalid" | "expired" | "email_not_linked";
type TrialKind = "ok" | "already_used" | "already_subscribed" | "unauthorized";

export type MockOptions = {
  profile?: ProfileKind;
  paymentCreate?: PaymentKind;
  sendCode?: SendCodeKind;
  verifyCode?: VerifyKind;
  trialActivate?: TrialKind;
};

function respondProfile(route: Route, kind: ProfileKind) {
  if (kind === "unauth") {
    return route.fulfill({
      status: 401,
      json: { ok: false, error: "unauthorized" },
    });
  }
  const profile =
    kind === "trial_used"
      ? makeProfile({ trialAvailable: false, subState: "none" })
      : kind === "active_sub"
        ? makeProfile({ trialAvailable: false, subState: "active" })
        : makeProfile({ trialAvailable: true, subState: "none" });
  return route.fulfill({ status: 200, json: { ok: true, ...profile } });
}

function respondPaymentCreate(route: Route, kind: PaymentKind) {
  if (kind === "unauthorized") {
    return route.fulfill({
      status: 401,
      json: { ok: false, error: "unauthorized" },
    });
  }
  if (kind === "payment_unavailable") {
    return route.fulfill({
      status: 200,
      json: {
        ok: true,
        status: "payment_unavailable",
        plan_id: "1m",
        plan_name: "1 месяц",
        device_count: 5,
        amount_rubes: 249,
        amount_kopecks: 24900,
        message: "Оплата временно недоступна",
      },
    });
  }
  return route.fulfill({
    status: 200,
    json: {
      ok: true,
      status: "ok",
      plan_id: "1m",
      plan_name: "1 месяц",
      device_count: 5,
      amount_rubes: 249,
      amount_kopecks: 24900,
      payment_url: "https://pay.example/order/123",
    },
  });
}

function respondSendCode(route: Route, kind: SendCodeKind) {
  if (kind === "disposable") {
    return route.fulfill({
      status: 200,
      json: { ok: false, error: "disposable_email" },
    });
  }
  if (kind === "rate_limited") {
    return route.fulfill({
      status: 200,
      json: { ok: false, error: "rate_limited" },
    });
  }
  return route.fulfill({
    status: 200,
    json: { ok: true, ttl_minutes: 10 },
  });
}

function respondVerify(route: Route, kind: VerifyKind) {
  const errorMap: Record<Exclude<VerifyKind, "ok">, string> = {
    invalid: "invalid_code",
    expired: "code_expired",
    email_not_linked: "email_not_linked",
  };
  if (kind === "ok") {
    return route.fulfill({ status: 200, json: { ok: true } });
  }
  return route.fulfill({ status: 200, json: { ok: false, error: errorMap[kind] } });
}

function respondTrial(route: Route, kind: TrialKind) {
  if (kind === "unauthorized") {
    return route.fulfill({
      status: 401,
      json: { ok: false, error: "unauthorized" },
    });
  }
  if (kind === "already_used") {
    return route.fulfill({
      status: 200,
      json: { ok: false, error: "trial_already_used" },
    });
  }
  if (kind === "already_subscribed") {
    return route.fulfill({
      status: 200,
      json: { ok: false, error: "already_subscribed" },
    });
  }
  return route.fulfill({
    status: 200,
    json: {
      ok: true,
      trial: { started_at: "2026-07-12T00:00:00Z", expires_at: "2026-07-15T00:00:00Z" },
      keys: {
        subscription_url: "https://bravada-connect.ru/sub/token123",
        servers: [
          {
            label: "Нидерланды",
            country_code: "NL",
            country_flag: "🇳🇱",
            vless_link: "vless://uuid@example.com:443?security=reality",
          },
        ],
      },
    },
  });
}

/** Зарегистрировать перехват API с заданными ответами. */
export async function mockApi(page: Page, opts: MockOptions = {}) {
  await page.route("**/api/v1/**", async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (url.includes("/api/v1/user/profile") && method === "GET") {
      return respondProfile(route, opts.profile ?? "unauth");
    }
    if (url.includes("/api/v1/payment/create") && method === "POST") {
      return respondPaymentCreate(route, opts.paymentCreate ?? "payment_url");
    }
    if (url.includes("/api/v1/auth/email/send-code") && method === "POST") {
      return respondSendCode(route, opts.sendCode ?? "ok");
    }
    if (url.includes("/api/v1/auth/email/verify") && method === "POST") {
      return respondVerify(route, opts.verifyCode ?? "ok");
    }
    if (url.includes("/api/v1/user/trial/activate") && method === "POST") {
      return respondTrial(route, opts.trialActivate ?? "ok");
    }
    // Безопасная заглушка для всего прочего.
    return route.fulfill({ status: 200, json: { ok: true } });
  });
}

/** Поставить сессионную cookie, чтобы middleware пропустил защищённый маршрут. */
export async function setSession(page: Page) {
  await page.context().addCookies([
    { name: "session", value: "e2e-session", domain: "localhost", path: "/" },
  ]);
}

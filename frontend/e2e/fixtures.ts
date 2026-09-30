import type { Page, Route } from "@playwright/test";

export const BASE_URL = "http://localhost:3100";

/** Валидный по формату тестовый токен подписки (см. tokenStore.TOKEN_RE). */
export const E2E_TOKEN = "e2e-token-1234567890abc";

/**
 * Тестовые данные и перехват API для E2E.
 *
 * Все запросы фронта к бэкенду (/api/v1/**) перехватываются через page.route()
 * и возвращают фиксированные ответы — реальный backend не нужен, тесты
 * детерминированы. Профиль определяет состояние авторизации/подписки:
 * сессия — Bearer-токен в памяти, поэтому «авторизован» в моках = пришёл
 * заголовок Authorization (как на бэкенде link-auth).
 */
export type ProfileResponse = {
  user: {
    telegram_user_id: number;
    email: string | null;
    internal_user_id: string;
    login_url?: string;
    subscription_url?: string;
  };
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
      email: null,
      internal_user_id: "u-1",
      login_url: `https://bravada-connect.ru/auth/${E2E_TOKEN}`,
      subscription_url: `https://bravada-connect.ru/sub/${E2E_TOKEN}`,
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

type ProfileKind = "auth" | "unauth" | "bad_token" | "trial_used" | "active_sub";
type PaymentKind = "payment_url" | "payment_unavailable" | "unauthorized";
type LinkRequestKind = "ok" | "rate_limited";

export type MockOptions = {
  profile?: ProfileKind;
  paymentCreate?: PaymentKind;
  linkRequest?: LinkRequestKind;
};

function respondProfile(route: Route, kind: ProfileKind) {
  // Без Bearer профиль недоступен; bad_token — недоступен и с Bearer
  // (отозванная ссылка), остальные сценарии валидны только с токеном.
  const authorization = route.request().headers()["authorization"] ?? "";
  const hasBearer = authorization.startsWith("Bearer ");
  if (kind === "unauth" || kind === "bad_token" || !hasBearer) {
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

function respondLinkRequest(route: Route, kind: LinkRequestKind) {
  if (kind === "rate_limited") {
    return route.fulfill({
      status: 429,
      json: { ok: false, error: "rate_limited" },
    });
  }
  return route.fulfill({ status: 200, json: { ok: true, subscription_url: `https://bravada-connect.ru/sub/${E2E_TOKEN}`, expires_at: "2026-10-02T12:00:00Z" } });
}

/** Зарегистрировать перехват API с заданными ответами. */
export async function mockApi(page: Page, opts: MockOptions = {}) {
  await page.route("**/api/v1/**", async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (url.includes("/api/v1/plans") && method === "GET") {
      return route.fulfill({json:{ok:true,plans:[
        ["1d",1,12],["7d",7,99],["14d",14,169],["1m",30,249],
        ["3m",90,699],["6m",180,1259],["365d",365,2199]
      ].map(([plan_id,duration_days,base_rubles])=>({plan_id,duration_days,base_rubles})),
      global_discount:null,custom_day_price_rubles:15,extra_device_price_rubles:80,devices:{base:5,max:20}}});
    }
    if (url.includes("/api/v1/user/profile") && method === "GET") {
      return respondProfile(route, opts.profile ?? "unauth");
    }
    if (url.includes("/api/v1/payment/create") && method === "POST") {
      return respondPaymentCreate(route, opts.paymentCreate ?? "payment_url");
    }
    if (url.includes("/api/v1/auth/link/request") && method === "POST") {
      return respondLinkRequest(route, opts.linkRequest ?? "ok");
    }
    // Безопасная заглушка для всего прочего.
    return route.fulfill({ status: 200, json: { ok: true } });
  });
}

/**
 * «Войти» по ссылке-подписке, как пользователь из письма: визит
 * /auth/<token>?next=… — токен попадает в память, страница валидирует его
 * профилем и уходит на next клиентской навигацией.
 */
export async function loginByLink(page: Page, next = "/dashboard") {
  await page.goto(`/auth/${E2E_TOKEN}?next=${encodeURIComponent(next)}`);
  await page.waitForURL(`**${next}`);
}

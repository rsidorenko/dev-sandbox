import { test, expect } from "@playwright/test";

const user = { internal_user_id: "fixture-user", telegram_user_id: 533520521, is_web: false, trial_used: true, is_internal: false, subscription_state: "active", is_active: true, active_until: "2026-10-01T10:00:00Z", remaining_days: 8, plan_id: "1m", device_count: 2, balance_rubles: 10 };
const detail = { user, promo_redemptions: [], traffic: null, diagnostics: { observed_at: "2026-09-23T10:00:00Z", source: "database", rotation_pending: true, pending_access_jobs: 1, live_access: "not_checked", access_jobs: [{ status: "pending", attempts: 3, created_at: "2026-09-23T10:00:00Z", updated_at: "2026-09-23T10:00:00Z", next_attempt_at: "2026-09-23T11:00:00Z" }], connection_checks: [], subscription_actions: [] } };

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => {
    const url = new URL(route.request().url());
    const data = url.pathname.endsWith("/add-days") ? { user, created: false, days_added: 7, new_active_until: user.active_until, provisioning: "success", servers_ok: 11 } : url.pathname.endsWith("/fixture-user") ? detail : url.pathname.endsWith("/users") ? {
      users: url.searchParams.has("offset") && Number(url.searchParams.get("offset")) > 0 ? [] : [user], total: 51,
      observed_at: "2026-09-23T10:00:00Z", facets: { status: [{ key: "active", count: 51 }] }, limit: 50, offset: 0,
    } : { promocodes: [] };
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, ...data }) });
  });
});

test("поиск, страницы и фильтры хранятся в URL, карточка открывается напрямую", async ({ page }) => {
  await page.goto("/admin/users");
  await page.getByRole("textbox", { name: "Поиск пользователей" }).fill("5335");
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  await expect(page).toHaveURL(/search=5335/);
  await page.getByRole("button", { name: "Сохранить фильтр" }).click();
  await page.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  await page.goBack();
  await expect(page.getByRole("link", { name: "533520521" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Поиск пользователей" })).toHaveValue("5335");
  await page.getByRole("link", { name: "533520521" }).click();
  await expect(page.getByRole("heading", { name: /Карточка пользователя/ })).toBeVisible();
  await expect(page.getByText("Требует внимания: ротация ключей ещё не завершена.")).toBeVisible();
  await expect(page.getByText(/Работа туннеля прямо сейчас не проверялась/)).toBeVisible();
  await expect(page.getByText(/Трафик за текущий месяц.*нет данных/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: /Карточка пользователя/ })).toBeVisible();
});

test("карточка на мобильном экране не раскрывает опасные действия", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/admin/users/fixture-user");
  await expect(page.getByRole("button", { name: "Добавить дни" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Пометить тестовым" })).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/admin-user-mobile.png", fullPage: true });
});

test("без сессии возврат после входа сохраняет фильтр", async ({ context, page }) => {
  await context.clearCookies();
  await page.goto("/admin/users?status=active&page=2");
  await expect(page).toHaveURL(/\/admin\/login/);
  expect(new URL(page.url()).searchParams.get("next")).toBe("/admin/users?status=active&page=2");
});

test("результат продления остаётся видимым после обновления карточки", async ({ page }) => {
  await page.goto("/admin/users/fixture-user");
  await page.getByRole("button", { name: "Добавить дни", exact: true }).click();
  await page.getByRole("button", { name: "Добавить дни", exact: true }).last().click();
  await expect(page.getByText(/Серверов синхронизировано: 11/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Добавить дни подписки" })).toBeVisible();
});

test("дробный номер страницы нормализуется до целого", async ({ page }) => {
  const request = page.waitForRequest(r => r.url().includes("/api/v1/admin/users?"));
  await page.goto("/admin/users?page=1.01");
  expect(new URL((await request).url()).searchParams.get("offset")).toBe("0");
  await expect(page.getByText("Страница 1 / 2")).toBeVisible();
});

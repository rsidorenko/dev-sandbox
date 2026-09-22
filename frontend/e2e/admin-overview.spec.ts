import { test, expect } from "@playwright/test";

const overview = { ok: true, observed_at: "2026-09-23T00:30:00Z", day: "2026-09-23", active_users: 63, new_users: 4, revenue_kopecks: 24900, payments: 1, unknown_amount_payments: 0, pending_access: 2, pending_rotations: 1, open_incidents: 3, hosts: 7, connections: 11, observations: { healthy: 8, error: 1, stale: 1, unknown: 1 }, attention_total: 1, issues: [{ key: "fixture", kind: "rotation", title: "Ротация ключей не завершена", internal_user_id: "user-fixture", severity: "warning" }] };

test("обзор связывает реальные показатели с рабочими разделами", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [], users: [], total: 0, plans: [], facets: { status: [], plan: [], balance: [] } }) }));
  await page.route("**/api/v1/admin/overview", route => route.fulfill({ contentType: "application/json", body: JSON.stringify(overview) }));
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Обзор сервиса" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Активный доступ 63/ })).toHaveAttribute("href", "/admin/users?status=active");
  await expect(page.getByRole("link", { name: /Выручка сегодня, RUB/ })).toContainText("249,00 ₽");
  await expect(page.getByRole("link", { name: "Разобраться →" })).toHaveAttribute("href", "/admin/users/user-fixture");
  await expect(page.locator("aside").getByRole("link", { name: "Обзор", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("Опрос статистики не подтверждает работу туннеля из сети пользователя.")).toBeVisible();
  await page.setViewportSize({ width: 320, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/admin-overview-mobile.png", fullPage: true });
  await page.getByRole("textbox", { name: "Найти пользователя" }).fill("12345");
  await page.getByRole("button", { name: "Открыть поиск" }).click();
  await expect(page).toHaveURL(/\/admin\/users\?search=12345$/);
});

test("ошибка обзора не превращается в нулевые показатели", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [] }) }));
  await page.route("**/api/v1/admin/overview", route => route.fulfill({ status: 500, body: "{}" }));
  await page.goto("/admin");
  await expect(page.locator("main").getByRole("alert")).toContainText("Не удалось получить данные");
  await expect(page.getByRole("heading", { name: "Активный доступ" })).not.toBeVisible();
});

test("вход без адреса возврата открывает обзор", async ({ page }) => {
  await page.route("**/api/v1/**", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [] }) }));
  await page.route("**/api/v1/admin/overview", route => route.fulfill({ contentType: "application/json", body: JSON.stringify(overview) }));
  await page.route("**/api/v1/admin/auth/login", route => route.fulfill({ contentType: "application/json", headers: { "set-cookie": "admin_session=test-session; Path=/; SameSite=Lax" }, body: JSON.stringify({ ok: true }) }));
  await page.goto("/admin/login");
  await page.getByLabel("Логин", { exact: true }).fill("test-admin");
  await page.getByLabel("Пароль", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole("heading", { name: "Обзор сервиса" })).toBeVisible();
  await page.getByRole("button", { name: "Сменить тему" }).click();
  await expect(page.getByRole("link", { name: /Активный доступ 63/ })).toHaveCSS("background-color", "rgb(39, 39, 42)");
  await page.screenshot({ path: "/tmp/admin-overview-desktop-dark.png", fullPage: true });
});

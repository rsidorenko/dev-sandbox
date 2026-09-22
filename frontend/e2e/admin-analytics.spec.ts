import { test, expect } from "@playwright/test";

const fixture = {
  ok: true, period: { date_from: "2026-09-01", date_to: "2026-09-23", bucket: "day" },
  kpi: { revenue_rubles: 249, payments_count: 1, paying_users: 1, new_users: 4, trials_started: 3, active_subscriptions: 2, traffic_gb: 5, traffic_users: 2 },
  series: { revenue: [{ bucket: "2026-09-23", rubles: 249 }], new_users: [{ bucket: "2026-09-23", count: 4 }], trials: [{ bucket: "2026-09-23", count: 3 }], traffic: [{ bucket: "2026-09-23", gb: 5, users: 2 }] },
  traffic_by_server: [{ label: "Германия", gb: 5, users: 2 }], by_plan: [{ plan_id: "1m", count: 2 }],
};

test("все графики сохранены, разделы не перезагружают данные и черновик", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  let requests = 0;
  await page.route("**/api/v1/**", route => {
    if (route.request().url().includes("/admin/stats?")) requests++;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(route.request().url().includes("/admin/stats?") ? fixture : { ok: true, promocodes: [] }) });
  });
  await page.goto("/admin/stats?period=30d&section=sales");
  await expect(page.getByRole("heading", { name: "Выручка", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Активные подписки по тарифам", exact: true })).toBeVisible();
  const count = requests;
  await page.getByRole("button", { name: "Активация и удержание", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Новые пользователи", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Активация триалов", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Качество VPN", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Трафик", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Трафик по серверам", exact: true })).toBeVisible();
  await expect(page.locator(".recharts-surface")).toHaveCount(2);
  await page.getByRole("button", { name: "Реклама", exact: true }).click();
  await page.getByRole("combobox", { name: "Кампания", exact: true }).fill("ads_draft");
  await page.getByRole("button", { name: "Продажи", exact: true }).click();
  await page.getByRole("button", { name: "Реклама", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Кампания", exact: true })).toHaveValue("ads_draft");
  expect(requests).toBe(count);
  await page.reload();
  await expect(page.getByRole("button", { name: "Реклама", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/period=30d/);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("ошибка API не отображается как нулевая выручка", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => route.fulfill({ status: route.request().url().includes("/admin/stats?") ? 500 : 200, contentType: "application/json", body: JSON.stringify(route.request().url().includes("/admin/stats?") ? { ok: false } : { ok: true, promocodes: [] }) }));
  await page.goto("/admin/stats");
  await expect(page.getByText("Не удалось загрузить статистику.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Выручка", exact: true })).toBeHidden();
});

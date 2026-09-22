import { test, expect } from "@playwright/test";

test("состояние сбора и подключений не маскирует отсутствие данных", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [], observed_at: "2026-09-23T10:00:00Z", source: "StatsService / БД", stale_after_seconds: 900, servers: ["healthy", "error", "stale", "unknown"].map((health, i) => ({ id: i, label: `Сервер ${i}`, server_host: "example.test", transport_type: i % 2 ? "xhttp" : "tcp", provider_kind: "bare_xray", client_surface: "default", is_active: true, health, checked_at: i === 3 ? null : "2026-09-23T10:00:00Z", last_success_at: null, traffic_collected_at: null, traffic_bytes: 0, traffic_keys: 0 })) }) }));
  await page.goto("/admin/servers");
  await expect(page.getByText("Данные получены", { exact: true })).toBeVisible();
  await expect(page.getByText("Ошибка опроса", { exact: true })).toBeVisible();
  await expect(page.getByText("Данные устарели", { exact: true })).toBeVisible();
  await expect(page.getByText("Нет наблюдений", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Требует внимания" }).check();
  await expect(page.getByText("Данные получены", { exact: true })).toBeHidden();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/admin-servers-mobile.png", fullPage: true });
});

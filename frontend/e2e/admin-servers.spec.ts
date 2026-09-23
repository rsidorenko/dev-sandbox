import { test, expect } from "@playwright/test";

test("состояние сбора и подключений не маскирует отсутствие данных", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, locations: [], profiles: [], recent: [], promocodes: [], observed_at: "2026-09-23T10:00:00Z", source: "StatsService / БД", stale_after_seconds: 900, servers: ["healthy", "error", "stale", "unknown"].map((health, i) => ({ id: i, label: `Сервер ${i}`, server_host: "example.test", transport_type: i % 2 ? "xhttp" : "tcp", provider_kind: "bare_xray", client_surface: "default", is_active: true, health, checked_at: i === 3 ? null : "2026-09-23T10:00:00Z", last_success_at: null, traffic_collected_at: null, traffic_bytes: 0, traffic_keys: 0 })) }) }));
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

test("история качества различает маршрут и ошибку измерителя на мобильном экране", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  const now = new Date().toISOString();
  await page.route("**/api/v1/**", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, servers: [], promocodes: [], source: "StatsService / БД", observed_at: now, stale_after_seconds: 900,
    locations: [{ location: "entry-de", enabled: true, started_at: now, active_until_utc: now, status: "measurement_error", reason: "collector_failed" }],
    profiles: [{ location: "entry-de", server_id: 1, route: 65001, label: "Автовыбор", attempts: 5, verified: 3, unverified: 1, measurement_errors: 1, errors: 0, slow: 1, p50_ms: 200, p95_ms: 1200, last_checked_at: now }],
    recent: [{ location: "entry-de", started_at: now, server_id: 1, route: 65001, label: "Автовыбор", result: "route_unknown", tls_ms: 150, ttfb_ms: 200 }] }) }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/admin/servers");
  await expect(page.getByText("200 мс / 1200 мс")).toBeVisible();
  await page.getByText("Последние результаты (до 100)").click();
  await expect(page.getByText("Маршрут не подтверждён", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/vpn-quality-mobile.png", fullPage: true });
});

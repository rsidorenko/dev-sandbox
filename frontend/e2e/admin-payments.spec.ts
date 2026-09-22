import { test, expect } from "@playwright/test";

test("оплата, подписка и выдача показаны раздельно", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [], observed_at: "2026-09-23T10:00:00Z", days: 30, page: 1, limit: 50, total: 1, payments: [{ row_id: "test", reference: "payment-fixture", occurred_at: "2026-09-23T10:00:00Z", provider: "yookassa", internal_user_id: "user-fixture", telegram_user_id: 12345, amount_minor_units: 24900, currency_code: "RUB", payment_status: "accepted", apply_status: "active_applied", access_status: "unknown", access_attempts: null, attention_reason: null }] }) }));
  await page.goto("/admin/payments");
  await expect(page.getByRole("cell", { name: "Факт принят", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Подписка применена", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Нет подтверждения выдачи", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "249.00 ₽", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "12345" })).toHaveAttribute("href", "/admin/users/user-fixture");
  await page.getByRole("checkbox", { name: "Требует внимания" }).click();
  await expect(page).toHaveURL(/attention=1/);
  await expect(page.getByRole("checkbox", { name: "Требует внимания" })).toBeChecked();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

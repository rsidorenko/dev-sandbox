import { test, expect } from "@playwright/test";

test.beforeEach(async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  await page.route("**/api/v1/**", route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [], users: [], total: 0, plans: [] }),
  }));
});

test("боковая навигация сохраняет переходы и тему", async ({ page }) => {
  await page.goto("/admin/users");
  const nav = page.locator("aside").getByRole("navigation");
  await expect(nav.getByRole("link", { name: "Пользователи" })).toHaveAttribute("aria-current", "page");
  await nav.getByRole("link", { name: "Промокоды" }).click();
  await expect(page).toHaveURL(/\/admin\/promocodes$/);
  await page.getByRole("button", { name: "Сменить тему" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(page.getByRole("button", { name: "Открыть меню админки" })).toBeHidden();
});

test("мобильное меню удерживает и возвращает фокус", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto("/admin/users");
  const trigger = page.getByRole("button", { name: "Открыть меню админки" });
  await page.locator("main").evaluate(node => { node.style.minHeight = "3000px"; });
  await trigger.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await page.mouse.move(310, 500);
  await page.mouse.wheel(0, 600);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await drawer.getByRole("button", { name: "Закрыть меню" }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(drawer.getByRole("link", { name: "Статистика" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await drawer.getByRole("link", { name: "Промокоды" }).click();
  await expect(page).toHaveURL(/\/admin\/promocodes$/);
  await expect(drawer).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/admin-nav-mobile.png" });
});

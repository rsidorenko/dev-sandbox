import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";

test.describe("Trial activation (/trial)", () => {
  test("unauthenticated visitor is redirected to /login", async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/trial");
    await expect(page).toHaveURL(/\/login\?next=\/trial$/);
  });

  test("successful activation shows the success state", async ({ page }) => {
    await mockApi(page, { profile: "auth", trialActivate: "ok" });
    await page.goto("/trial");
    await expect(page.getByText(/Готово!/)).toBeVisible();
    await expect(page.getByText(/3 дня начнутся после первого подключения/)).toBeVisible();
  });

  test("already-used trial shows the 'used' state via precheck", async ({ page }) => {
    await mockApi(page, { profile: "trial_used" });
    await page.goto("/trial");
    await expect(page.getByText("Пробный период уже использован")).toBeVisible();
  });

  test("active subscription shows the 'already subscribed' state", async ({ page }) => {
    await mockApi(page, { profile: "active_sub" });
    await page.goto("/trial");
    await expect(page.getByText("У вас уже есть активная подписка")).toBeVisible();
  });
});

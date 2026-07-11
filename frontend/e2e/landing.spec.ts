import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";
import { LandingPage } from "./pom/LandingPage";

test.use({});

test.describe("Landing", () => {
  test.beforeEach(async ({ page }) => {
    // Лендинг не зависит от бэкенда, но root layout всё равно тянет /profile —
    // перехватываем, чтобы не было запросов в несуществующий backend.
    await mockApi(page, { profile: "unauth" });
  });

  test("renders hero, tariff section and trial link", async ({ page }) => {
    const landing = new LandingPage(page);
    await landing.goto();
    await expect(landing.heroHeading()).toBeVisible();
    await expect(landing.tariffsSection()).toBeVisible();
    await expect(
      page.getByRole("link", { name: /3 дня бесплатно/ }).first(),
    ).toBeVisible();
    await expect(landing.choosePlanButton()).toBeVisible();
  });

  test("footer document link navigates to /offer", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Публичная оферта" }).click();
    await expect(page).toHaveURL(/\/offer$/);
  });

  test("selecting a tariff and clicking CTA opens checkout", async ({ page }) => {
    const landing = new LandingPage(page);
    await landing.goto();
    await landing.tariffTab("1 год").click();
    await landing.checkoutCta().click();
    await expect(page).toHaveURL(/\/payment\/365d$/);
  });

  test("'3 дня бесплатно' link points to /trial", async ({ page }) => {
    await page.goto("/");
    const trialLink = page
      .getByRole("link", { name: /3 дня бесплатно/ })
      .first();
    await expect(trialLink).toHaveAttribute("href", "/trial");
  });
});

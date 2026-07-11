import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";

test.describe("Navigation & status pages", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
  });

  test("/success renders the success heading", async ({ page }) => {
    await page.goto("/success");
    await expect(
      page.getByRole("heading", { name: "Оплата прошла успешно!" }),
    ).toBeVisible();
  });

  test("/fail renders the failure heading", async ({ page }) => {
    await page.goto("/fail");
    await expect(
      page.getByRole("heading", { name: "Оплата не прошла" }),
    ).toBeVisible();
  });

  test("/contacts is reachable from the header", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Контакты" }).first().click();
    await expect(page).toHaveURL(/\/contacts$/);
  });

  test("/instructions is reachable from the header", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Инструкция по подключению" }).first().click();
    await expect(page).toHaveURL(/\/instructions$/);
  });
});

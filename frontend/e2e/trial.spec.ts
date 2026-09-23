import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";

test.describe("Trial stub (/trial)", () => {
  test("anonymous visitor sees the bot-only stub without redirect to /login", async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/trial");
    await expect(page).toHaveURL(/\/trial$/);
    await expect(page.getByText("Пробный период выдаётся в Telegram-боте")).toBeVisible();
  });

  test("primary button points to the bot deeplink", async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/trial");
    const botButton = page.getByRole("link", { name: /Открыть бота и получить 3 дня бесплатно/ });
    await expect(botButton).toBeVisible();
    await expect(botButton).toHaveAttribute(
      "href",
      /^https:\/\/t\.me\/bravadavpn_bot\?start=trial$/,
    );
  });

  test("no activation request is sent — page is static", async ({ page }) => {
    await mockApi(page, { profile: "auth" });
    let activateCalls = 0;
    await page.route("**/api/v1/user/trial/activate", (route) => {
      activateCalls += 1;
      return route.fulfill({ status: 404, json: { ok: false } });
    });
    await page.goto("/trial");
    await expect(page.getByText("Пробный период выдаётся в Telegram-боте")).toBeVisible();
    expect(activateCalls).toBe(0);
  });
});

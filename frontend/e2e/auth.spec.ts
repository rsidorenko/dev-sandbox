import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";
import { LoginPage } from "./pom/LoginPage";

test.describe("Auth", () => {
  test("login flow: email → code → redirect to next", async ({ page }) => {
    await mockApi(page, {
      profile: "unauth",
      sendCode: "ok",
      verifyCode: "ok",
    });
    const login = new LoginPage(page);
    // next=/ — не защищённый маршрут, чтобы успех verify корректно навигировал.
    await login.goto("/");
    await login.emailInput().fill("user@example.com");
    await login.sendCodeButton().click();
    await expect(login.codeStepHint()).toBeVisible();
    await login.codeInput().fill("123456");
    await login.verifyButton().click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("disposable email shows an explanatory alert", async ({ page }) => {
    await mockApi(page, { profile: "unauth", sendCode: "disposable" });
    const login = new LoginPage(page);
    await login.goto("/");
    await login.emailInput().fill("user@temp.mail");
    await login.sendCodeButton().click();
    await expect(page.getByText(/временной почтой/)).toBeVisible();
  });

  test("invalid verification code shows an error", async ({ page }) => {
    await mockApi(page, {
      profile: "unauth",
      sendCode: "ok",
      verifyCode: "invalid",
    });
    const login = new LoginPage(page);
    await login.goto("/");
    await login.emailInput().fill("user@example.com");
    await login.sendCodeButton().click();
    await login.codeInput().fill("000000");
    await login.verifyButton().click();
    await expect(page.getByText(/Неверный код/)).toBeVisible();
  });

  test("middleware redirects /dashboard to /login without a session cookie", async ({
    page,
  }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/dashboard");
    // Next middleware кодирует «/» → %2F в query; покрываем оба варианта.
    await expect(page).toHaveURL(/\/login\?next=(?:%2F|\/)dashboard$/);
  });
});

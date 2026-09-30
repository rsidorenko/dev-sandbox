import { expect, test } from "@playwright/test";
import { E2E_TOKEN, mockApi } from "./fixtures";
import { LoginPage } from "./pom/LoginPage";

test.describe("Auth (link-auth)", () => {
  test("login by pasted subscription link → redirect to next", async ({ page }) => {
    await mockApi(page, { profile: "auth" });
    const login = new LoginPage(page);
    await login.goto("/dashboard");
    await login.linkInput().fill(LoginPage.validLink());
    await login.loginButton().click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("revoked link shows an error and stays on /login", async ({ page }) => {
    await mockApi(page, { profile: "bad_token" });
    const login = new LoginPage(page);
    await login.goto("/dashboard");
    await login.linkInput().fill(LoginPage.validLink());
    await login.loginButton().click();
    await expect(page.getByText(/недействительна, отозвана/)).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("/auth/<token> validates the token, strips it from the URL and redirects", async ({
    page,
  }) => {
    await mockApi(page, { profile: "auth" });
    await page.goto(`/auth/${E2E_TOKEN}?next=/trial`);
    // Предикат по pathname: regex /\/trial$/ ложно матчит сам /auth-URL
    // (?next=/trial заканчивается на «/trial»).
    await expect(page).toHaveURL((url: URL) => url.pathname === "/trial");
    // Токен не должен оставаться в адресной строке/истории.
    expect(page.url()).not.toContain(E2E_TOKEN);
  });

  test("/auth/<token> with an unknown token shows the invalid-link state", async ({
    page,
  }) => {
    await mockApi(page, { profile: "bad_token" });
    await page.goto(`/auth/${E2E_TOKEN}?next=/dashboard`);
    await expect(page.getByText("Ссылка недействительна")).toBeVisible();
  });

  test("выдача ссылки на экране", async ({ page }) => {
    await mockApi(page, { profile: "unauth", linkRequest: "ok" });
    const login = new LoginPage(page);
    await login.goto("/");
    await login.sendLinkButton().click();
    await expect(login.sentHint()).toBeVisible();
  });

  test("ограничение частоты выдачи ссылки", async ({ page }) => {
    await mockApi(page, { profile: "unauth", linkRequest: "rate_limited" });
    const login = new LoginPage(page);
    await login.goto("/");
    await login.sendLinkButton().click();
    await expect(page.getByText(/Слишком много запросов/)).toBeVisible();
  });

  test("/dashboard without a session redirects to /login (client-side)", async ({
    page,
  }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/dashboard");
    // Сессия — токен в памяти (никаких кук): гвард делает AuthProvider.
    await expect(page).toHaveURL(/\/login/);
  });
});

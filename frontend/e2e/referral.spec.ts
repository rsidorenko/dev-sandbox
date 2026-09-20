import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures";

// ReferralCapture writes ?ref= to localStorage inside a client useEffect (mounted
// in (site)/layout.tsx). That effect runs AFTER hydration, so the e2e must POLL
// localStorage rather than read it immediately after page.goto — otherwise the
// assertion races the effect (deterministic fail on the heavy static landing page,
// flaky on /login, /payment).
async function expectCapturedRef(page: Page, expected: string) {
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("pending_referral_code")), {
      timeout: 5000,
      message: `pending_referral_code should become "${expected}"`,
    })
    .toBe(expected);
}

test.describe("Referral capture", () => {
  test("stores the ref param in localStorage as pending_referral_code", async ({
    page,
  }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/?ref=ABC123");
    await expectCapturedRef(page, "ABC123");
  });

  test("does not write when there is no ref param", async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/");
    const code = await page.evaluate(() =>
      localStorage.getItem("pending_referral_code"),
    );
    expect(code).toBeNull();
  });

  test("captures ref on /login (not only on /)", async ({ page }) => {
    // ReferralCapture is mounted in the root layout, so any route captures ?ref=.
    await mockApi(page, { profile: "unauth" });
    await page.goto("/login?ref=LOGIN123");
    await expectCapturedRef(page, "LOGIN123");
  });

  test("captures ref on /dashboard even when redirected to /login (no session)", async ({
    page,
  }) => {
    // Сессия — токен в памяти, middleware-гварда больше нет: /dashboard?ref=
    // без токена клиентски уходит на /login, но ReferralCapture в layout
    // успевает захватить ref.
    await mockApi(page, { profile: "unauth" });
    await page.goto("/dashboard?ref=DASH456");
    await expect(page).toHaveURL(/\/login/);
    await expectCapturedRef(page, "DASH456");
  });

  test("captures ref on /payment (AuthGate path)", async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/payment/1m?ref=PAY789");
    await expectCapturedRef(page, "PAY789");
  });
});

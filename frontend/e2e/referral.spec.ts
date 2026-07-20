import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";

test.describe("Referral capture", () => {
  test("stores the ref param in localStorage as pending_referral_code", async ({
    page,
  }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/?ref=ABC123");
    const code = await page.evaluate(() =>
      localStorage.getItem("pending_referral_code"),
    );
    expect(code).toBe("ABC123");
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
    const code = await page.evaluate(() =>
      localStorage.getItem("pending_referral_code"),
    );
    expect(code).toBe("LOGIN123");
  });

  test("preserves ref across middleware redirect on protected route", async ({
    page,
  }) => {
    // /dashboard?ref= without a session → middleware redirects to
    // /login?next=/dashboard&ref=... — the ref must survive and be captured.
    await mockApi(page, { profile: "unauth" });
    await page.goto("/dashboard?ref=DASH456");
    expect(page.url()).toContain("/login");
    expect(page.url()).toContain("ref=DASH456");
    const code = await page.evaluate(() =>
      localStorage.getItem("pending_referral_code"),
    );
    expect(code).toBe("DASH456");
  });

  test("captures ref on /payment (AuthGate path)", async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
    await page.goto("/payment/1m?ref=PAY789");
    const code = await page.evaluate(() =>
      localStorage.getItem("pending_referral_code"),
    );
    expect(code).toBe("PAY789");
  });
});

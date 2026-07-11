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
});

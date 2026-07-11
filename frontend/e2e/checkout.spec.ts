import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";
import { CheckoutPage } from "./pom/CheckoutPage";

test.describe("Checkout (/payment/[planId])", () => {
  test("unauthenticated visitor sees the AuthGate", async ({ page }) => {
    await mockApi(page, { profile: "unauth" });
    const checkout = new CheckoutPage(page);
    await checkout.goto("1m");
    await expect(checkout.emailGateLink()).toHaveAttribute(
      "href",
      "/login?next=/payment/1m",
    );
    await expect(checkout.botGateLink()).toHaveAttribute(
      "href",
      /^https:\/\/t\.me\/.+bot\?start=1m$/,
    );
  });

  test("unknown plan shows 'Тариф не найден'", async ({ page }) => {
    await mockApi(page, { profile: "auth" });
    const checkout = new CheckoutPage(page);
    await checkout.goto("nope");
    await expect(checkout.notFound()).toBeVisible();
  });

  test("device slider updates the total", async ({ page }) => {
    await mockApi(page, { profile: "auth" });
    const checkout = new CheckoutPage(page);
    await checkout.goto("1m");
    await expect(checkout.heading()).toBeVisible();
    // 5 доп. устройств × round(80/30*30)=80 → +400 → 249+400 = 649.
    await checkout.deviceSlider().fill("10");
    await expect(checkout.payButton(649)).toBeVisible();
  });

  test("successful create redirects to the payment provider URL", async ({ page }) => {
    await mockApi(page, { profile: "auth", paymentCreate: "payment_url" });
    // Внешний редирект на платёжку — заглушаем, чтобы навигация не падала.
    await page.route("https://pay.example/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<h1>pay</h1>" }),
    );
    const checkout = new CheckoutPage(page);
    await checkout.goto("1m");
    await checkout.payButton().click();
    await expect(page).toHaveURL("https://pay.example/order/123");
  });

  test("payment_unavailable shows an informational alert", async ({ page }) => {
    await mockApi(page, { profile: "auth", paymentCreate: "payment_unavailable" });
    const checkout = new CheckoutPage(page);
    await checkout.goto("1m");
    await checkout.payButton().click();
    await expect(page.getByText("Оплата временно недоступна")).toBeVisible();
  });

  test("custom plan renders the calculated price", async ({ page }) => {
    await mockApi(page, { profile: "auth" });
    const checkout = new CheckoutPage(page);
    await checkout.goto("custom:10");
    // 10 дней × 15 ₽ = 150 ₽.
    await expect(checkout.payButton(150)).toBeVisible();
  });
});

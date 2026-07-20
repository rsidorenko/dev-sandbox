import type { Locator, Page } from "@playwright/test";

/** Page Object для страницы оформления подписки /payment/[planId]. */
export class CheckoutPage {
  constructor(private readonly page: Page) {}

  goto(planId: string) {
    return this.page.goto(`/payment/${planId}`);
  }

  heading(): Locator {
    return this.page.getByRole("heading", { name: "Оформление подписки" });
  }

  notFound(): Locator {
    return this.page.getByText("Тариф не найден");
  }

  payButton(amount?: number): Locator {
    // Card-pay button text is "Оплатить картой N ₽" (the balance-pay button is
    // "Оплатить с баланса …" — matched separately). Without an amount, default to
    // the card button.
    const name =
      amount === undefined
        ? /Оплатить картой/
        : new RegExp(`Оплатить.*${amount} ₽`);
    return this.page.getByRole("button", { name });
  }

  deviceSlider(): Locator {
    return this.page.locator('input[type="range"]');
  }

  emailGateLink(): Locator {
    return this.page.getByRole("link", { name: "Войти по email" });
  }

  botGateLink(): Locator {
    return this.page.getByRole("link", { name: /Telegram-бот/ });
  }
}

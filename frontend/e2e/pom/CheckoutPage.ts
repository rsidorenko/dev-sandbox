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
    // Основной CTA — «Перейти к оплате N ₽»; balance-кнопка матчится отдельно.
    const name =
      amount === undefined
        ? /Перейти к оплате/
        : new RegExp(`Перейти к оплате ${amount} ₽`);
    return this.page.getByRole("button", { name });
  }

  showDeviceOptions(): Locator {
    return this.page.getByRole("button", { name: "Нужно больше?" });
  }

  deviceSlider(): Locator {
    return this.page.locator('input[type="range"]');
  }

  cryptoPayButton(): Locator {
    return this.page.getByRole("button", { name: /Оплатить криптовалютой/ });
  }

  emailGateLink(): Locator {
    return this.page.getByRole("link", { name: "Войти по email" });
  }

  botGateLink(): Locator {
    return this.page.getByRole("link", { name: /Telegram-бот/ });
  }
}

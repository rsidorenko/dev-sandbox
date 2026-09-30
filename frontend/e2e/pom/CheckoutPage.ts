import type { Locator, Page } from "@playwright/test";
import { E2E_TOKEN } from "../fixtures";

/** Page Object для страницы оформления подписки /payment/[planId]. */
export class CheckoutPage {
  constructor(private readonly page: Page) {}

  /**
   * Вход по ссылке (как из письма) → клиентский редирект на чекаут: токен
   * живёт в памяти, полная перезагрузка страницы его теряет, поэтому
   * «авторизованные» сценарии начинаются с /auth/<token>?next=…
   */
  async goto(planId: string, opts: { query?: string; wait?: string } = {}) {
    const next = `/payment/${planId}${opts.query ?? ""}`;
    await this.page.goto(`/auth/${E2E_TOKEN}?next=${encodeURIComponent(next)}`);
    await this.page.waitForURL(opts.wait ?? `**${next}`);
  }

  /** Прямая навигация без сессии (сценарий AuthGate). */
  gotoDirect(planId: string) {
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

  gateLinkInput(): Locator {
    return this.page.getByLabel("Ссылка подписки", { exact: true });
  }

  gateSubmitButton(): Locator {
    return this.page.getByRole("button", { name: "Получить ссылку подписки" });
  }

  gateSentHint(): Locator {
    return this.page.getByLabel("Ваша ссылка");
  }

  botGateLink(): Locator {
    return this.page.getByRole("link", { name: /Telegram-бот/ });
  }
}

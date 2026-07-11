import type { Locator, Page } from "@playwright/test";

/** Page Object для главной (лендинг). */
export class LandingPage {
  constructor(private readonly page: Page) {}

  goto() {
    return this.page.goto("/");
  }

  heroHeading(): Locator {
    return this.page.getByRole("heading", { name: /Свободный интернет/ });
  }

  /** Кнопка-таб тарифа (по части подписи, устойчиво к бейджам «−N%»/«Лучшая цена»). */
  tariffTab(label: string): Locator {
    return this.page.getByRole("button", { name: new RegExp(label) });
  }

  /** CTA «Оформить подписку» активной карточки тарифа. */
  checkoutCta(): Locator {
    return this.page.getByRole("link", { name: "Оформить подписку" });
  }

  choosePlanButton(): Locator {
    return this.page.getByRole("link", { name: "Выбрать тариф" }).first();
  }

  tariffsSection(): Locator {
    return this.page.locator("#tariffs");
  }
}

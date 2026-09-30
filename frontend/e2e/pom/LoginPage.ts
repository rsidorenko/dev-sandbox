import type { Locator, Page } from "@playwright/test";
import { E2E_TOKEN } from "../fixtures";

/** Page Object для страницы входа /login (вход по ссылке-подписке). */
export class LoginPage {
  constructor(private readonly page: Page) {}

  goto(next?: string) {
    const url = next ? `/login?next=${encodeURIComponent(next)}` : "/login";
    return this.page.goto(url);
  }

  /** Готовая валидная ссылка-подписка для вставки в поле входа. */
  static validLink(): string {
    return `https://bravada-connect.ru/sub/${E2E_TOKEN}`;
  }

  linkInput(): Locator {
    return this.page.getByLabel(/^Ссылка подписки$/i);
  }

  loginButton(): Locator {
    return this.page.getByRole("button", { name: "Продолжить", exact: true });
  }

  sendLinkButton(): Locator {
    return this.page.getByRole("button", { name: "Получить ссылку подписки" });
  }

  sentHint(): Locator {
    return this.page.getByLabel("Ваша ссылка");
  }
}

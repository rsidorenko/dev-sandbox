import type { Locator, Page } from "@playwright/test";

/** Page Object для страницы входа /login. */
export class LoginPage {
  constructor(private readonly page: Page) {}

  goto(next?: string) {
    const url = next ? `/login?next=${encodeURIComponent(next)}` : "/login";
    return this.page.goto(url);
  }

  emailInput(): Locator {
    return this.page.getByLabel(/Email/i);
  }

  codeInput(): Locator {
    return this.page.getByLabel(/Код подтверждения/i);
  }

  sendCodeButton(): Locator {
    return this.page.getByRole("button", { name: "Получить код" });
  }

  verifyButton(): Locator {
    return this.page.getByRole("button", { name: "Войти" });
  }

  codeStepHint(): Locator {
    return this.page.getByText(/Код подтверждения отправлен/);
  }
}

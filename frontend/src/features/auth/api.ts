import { apiFetch } from "@/shared/api/client";
import type { LinkRequestResponse } from "@/entities/auth/types";

export const authApi = {
  /**
   * Запросить ссылку-подписку на почту (путь новых пользователей без ссылки).
   * Почта используется только как канал доставки и нигде не сохраняется;
   * referral_code атрибутируется при создании аккаунта на бэкенде.
   */
  requestLink: (email: string, referral_code?: string, next?: string) =>
    apiFetch<LinkRequestResponse>("/api/v1/auth/link/request", {
      method: "POST",
      body: JSON.stringify({
        email,
        ...(referral_code && { referral_code }),
        ...(next && { next }),
      }),
    }),
};

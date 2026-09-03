import { apiFetch } from "@/shared/api/client";

export type PromoDiscountKind = "percent" | "fixed_rubles";

/** Закреплённый промокод пользователя (TTL 24ч) — дескриптор скидки для отображения. */
export interface AppliedPromo {
  code: string;
  discount_kind: PromoDiscountKind;
  discount_value: number;
  /** Per-tariff карта {plan_id: value}; null — плоская мода (discount_value). */
  discounts_by_plan: Record<string, number> | null;
  /** Человекочитаемая подпись: «−15%», «−200 ₽» или «по тарифам». */
  discount_label: string;
  /** ISO-дата истечения закрепления (now+24ч). */
  expires_at: string;
  /** Сколько часов ещё действует закрепление. */
  remaining_hours: number;
  /** true, если код всё ещё прошёл повторную валидацию (активен/в сроке/аудитория). */
  valid: boolean;
}

export interface PromoState {
  ok: boolean;
  applied: AppliedPromo | null;
}

/** Preview скидки против конкретного тарифа (apply с plan_id+device_count). */
export interface PromoPreview {
  plan_id: string;
  device_count: number;
  base_rubles: number;
  discounted_rubles: number;
  discount_kopecks: number;
}

export interface ApplyPromoResponse extends PromoState {
  preview?: PromoPreview;
}

/** Карта серверных кодов ошибок → пользовательский текст (единая с ботом Фазы 3). */
export const PROMO_ERROR_MESSAGES: Record<string, string> = {
  not_found: "Промокод не найден или больше неактивен.",
  invalid_format: "Неверный формат кода (3–32 символа: латиница и цифры).",
  expired: "Срок действия промокода истёк.",
  exhausted: "Лимит промокода исчерпан.",
  already_used: "Вы уже активировали этот промокод ранее.",
  ineligible: "Промокод недоступен.",
  wrong_audience: "Этот промокод недоступен для вашей учётной записи.",
  channel_subscription_required: "Промокоды доступны подписчикам нашего Telegram-канала.",
};

export const promoApi = {
  get: () => apiFetch<PromoState>("/api/v1/user/promocode"),
  apply: (code: string, planId?: string, deviceCount?: number) =>
    apiFetch<ApplyPromoResponse>("/api/v1/user/promocode/apply", {
      method: "POST",
      body: JSON.stringify({ code, plan_id: planId, device_count: deviceCount }),
    }),
  clear: () => apiFetch<PromoState>("/api/v1/user/promocode/clear", { method: "POST" }),
};

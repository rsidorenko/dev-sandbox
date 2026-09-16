import { apiFetch } from "@/shared/api/client";

/** Статус клейма бонуса рассылки (считается сервером из claims-таблиц). */
export type ClaimStatus = "eligible" | "claimed" | "expired";

/** Бонус рассылки в payload уведомления. code НЕ приходит (приватен — клейм идёт по claims). */
export interface NotificationBonus {
  type: "days" | "promo";
  days?: number;
  label: string;
  ttl_days?: number;
}

export interface NotificationAction {
  href: string;
  label: string;
}

export interface WebNotification {
  id: number;
  kind:
    | "broadcast"
    | "trial_expiring"
    | "trial_expired"
    | "subscription_expiring"
    | "subscription_expired"
    | "key_deleted"
    | "referral_offer";
  title: string;
  /** Broadcast — Telegram-HTML (санитайзится на клиенте); прочие — plain text. */
  body: string | null;
  payload: {
    job_id?: string;
    bonus?: NotificationBonus;
    action?: NotificationAction;
    notification_type?: string;
  };
  created_at: string;
  read_at: string | null;
  claim_status?: ClaimStatus;
}

export interface NotificationsListResponse {
  ok: boolean;
  items: WebNotification[];
  unread_count: number;
  has_more: boolean;
}

export interface NotificationsReadResponse {
  ok: boolean;
  updated: number;
}

/** Успешный клейм: days → days/new_active_until; promo → code. */
export interface ClaimOkResponse {
  ok: boolean;
  status: "ok" | "already_claimed";
  days?: number;
  new_active_until?: string;
  code?: string;
}

/** Карта серверных кодов ошибок клейма → пользовательский текст. */
export const CLAIM_ERROR_MESSAGES: Record<string, string> = {
  already_claimed: "Бонус уже получен.",
  expired: "Срок получения бонуса истёк.",
  no_bonus: "В этом уведомлении нет бонуса.",
  rate_limited: "Слишком часто. Попробуйте чуть позже.",
  not_found: "Уведомление не найдено.",
  channel_subscription_required: "Бонус доступен подписчикам нашего Telegram-канала.",
  network_error: "Ошибка сети. Попробуйте позже.",
};

export const notificationsApi = {
  list: (limit = 20, offset = 0) =>
    apiFetch<NotificationsListResponse>(
      `/api/v1/user/notifications?limit=${limit}&offset=${offset}`,
    ),
  read: (ids: number[]) =>
    apiFetch<NotificationsReadResponse>("/api/v1/user/notifications/read", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  readAll: () =>
    apiFetch<NotificationsReadResponse>("/api/v1/user/notifications/read", {
      method: "POST",
      body: JSON.stringify({ all: true }),
    }),
  claim: (notificationId: number) =>
    apiFetch<ClaimOkResponse>(
      `/api/v1/user/notifications/${notificationId}/claim`,
      { method: "POST" },
    ),
};

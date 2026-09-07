// Типы ответов админ-API (/api/v1/admin/*).

export interface AdminUser {
  internal_user_id: string;
  telegram_user_id: number;
  email: string | null;
  trial_used: boolean;
  subscription_state: string | null;
  is_active: boolean;
  active_until: string | null;
  remaining_days: number | null;
  plan_id: string | null;
  device_count: number | null;
  balance_rubles: number;
}

export interface AdminUsersResponse {
  users: AdminUser[];
  limit: number;
  offset: number;
  total: number;
}

export interface AdminAddDaysResponse {
  user: AdminUser;
  /** true — подписка создана «с нуля» (custom:N, 5 устройств); false — продлена/реактивирована. */
  created: boolean;
  days_added: number;
  old_active_until: string | null;
  new_active_until: string;
  plan_unchanged: string | null;
  device_count_unchanged: number | null;
  provisioning: string;
  servers_ok: number;
  correlation_id: string;
}

export type PromoAudience = "new_users" | "existing_users" | "all_users";
export type PromoDiscountKind = "percent" | "fixed_rubles";

export interface AdminPromocode {
  code: string;
  audience: PromoAudience;
  discount_kind: PromoDiscountKind;
  discount_value: number;
  /** Per-tariff скидки {plan_id: value}; null — плоская мода (discount_value). */
  discounts_by_plan: Record<string, number> | null;
  max_redemptions: number | null;
  /** Лимит активаций одним пользователем: null — неограничено, 1 — единожды. */
  max_redemptions_per_user: number | null;
  redemptions_count: number;
  expires_at: string | null;
  is_active: boolean;
  note: string | null;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
}

export interface AdminPromocodesResponse {
  promocodes: AdminPromocode[];
  limit: number;
  offset: number;
}

export interface AdminPromocodePayload {
  code: string;
  audience: PromoAudience;
  discount_kind: PromoDiscountKind;
  /** Плоская мода. mutually exclusive с discounts_by_plan. */
  discount_value?: number | null;
  /** Per-tariff мода {plan_id: value}. mutually exclusive с discount_value. */
  discounts_by_plan?: Record<string, number> | null;
  max_redemptions?: number | null;
  /** Лимит активаций одним пользователем: null — неограничено, 1 — единожды. */
  max_redemptions_per_user?: number | null;
  expires_at?: string | null;
  note?: string | null;
  is_active?: boolean;
}

// /api/v1/admin/plans — вкладка «Тарифы»: цены 7 планов + глобальная скидка.

export interface AdminPlanPrice {
  plan_id: string;
  duration_days: number;
  /** Код-дефолт из domain/plans.py (не редактируется). */
  default_price_rubles: number;
  /** Эффективная цена (оверрайд или дефолт). */
  price_rubles: number;
  is_overridden: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface AdminGlobalDiscount {
  is_active: boolean;
  /** percent | fixed_rubles; null — выключенная скидка без сохранённых параметров. */
  discount_kind: PromoDiscountKind | null;
  /** При is_active=false может быть null. */
  discount_value: number | null;
  public_label: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface AdminPlansResponse {
  plans: AdminPlanPrice[];
  global_discount: AdminGlobalDiscount;
}

export interface AdminGlobalDiscountPayload {
  is_active: boolean;
  discount_kind?: PromoDiscountKind | null;
  discount_value?: number | null;
  public_label?: string | null;
}

// /api/v1/admin/stats — агрегированный дашборд статистики за период.

export type AdminStatsPeriod = "1d" | "7d" | "30d" | "90d" | "all";

export interface AdminStatsPeriodMeta {
  key: AdminStatsPeriod;
  /** ISO (UTC) — старт окна. */
  from: string;
  /** ISO (UTC) — now на момент запроса. */
  to: string;
  /** Бакет группировки рядов: hour | day | month. */
  bucket: "hour" | "day" | "month";
}

export interface AdminStatsKpi {
  revenue_rubles: number;
  payments_count: number;
  paying_users: number;
  new_users: number;
  trials_started: number;
  /** Текущий живой gauge, не за период. */
  active_subscriptions: number;
  /** Доля триалов за период, дошедших до оплаты; null — если триалов не было. */
  trial_to_paid_rate: number | null;
  /** Трафик bare_xray за период (ГБ, up+down). */
  traffic_gb: number;
  /** Максимум уникальных пользователей в сутки за период. */
  traffic_users: number;
}

export interface AdminStatsRevenuePoint {
  /** ISO локального бакета (без зоны); формат фронт берёт из period.bucket. */
  bucket: string;
  rubles: number;
  count: number;
}

export interface AdminStatsCountPoint {
  bucket: string;
  count: number;
}

export interface AdminStatsTrafficPoint {
  /** stat_date — DATE в MSK (день, независимо от period.bucket). */
  bucket: string;
  gb: number;
  users: number;
}

export interface AdminStatsTrafficByServer {
  /** label из vpn_servers; для неизвестных id — «server <id>». */
  label: string;
  gb: number;
  users: number;
}

export interface AdminStatsByPlan {
  plan_id: string;
  count: number;
}

export interface AdminStatsResponse {
  ok: true;
  period: AdminStatsPeriodMeta;
  kpi: AdminStatsKpi;
  series: {
    revenue: AdminStatsRevenuePoint[];
    new_users: AdminStatsCountPoint[];
    trials: AdminStatsCountPoint[];
    traffic: AdminStatsTrafficPoint[];
  };
  traffic_by_server: AdminStatsTrafficByServer[];
  by_plan: AdminStatsByPlan[];
}

// --- Рассылка (/api/v1/admin/broadcast/*) ---------------------------------

/** Ключи категорий аудитории (множественный выбор → UNION на бэке). */
export type BroadcastCategory =
  | "all"
  | "active"
  | "expired_0_2d"
  | "expired_3_5d"
  | "expired_6_7d"
  | "expired_8_30d"
  | "expired_30plus"
  | "dormant";

/** Человекочитаемые подписи категорий (порядок вывода в UI). */
export const BROADCAST_CATEGORY_LABELS: { key: BroadcastCategory; label: string }[] = [
  { key: "all", label: "Все пользователи" },
  { key: "active", label: "Активные (платные + триал)" },
  { key: "dormant", label: "Не активировали триал (спящие)" },
  { key: "expired_0_2d", label: "Подписка истекла 0–2 дня назад" },
  { key: "expired_3_5d", label: "Подписка истекла 3–5 дней назад" },
  { key: "expired_6_7d", label: "Подписка истекла 6–7 дней назад" },
  { key: "expired_8_30d", label: "Подписка истекла 8–30 дней назад" },
  { key: "expired_30plus", label: "Подписка истекла более 30 дней назад" },
];

export interface BroadcastEstimate {
  total: number;
  per_category_counts: Partial<Record<BroadcastCategory, number>>;
  explicit_resolved: number[];
  explicit_unresolved: string[];
}

export type BroadcastJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "interrupted"
  | "cancelled";

export interface BroadcastJob {
  job_id: string;
  created_at: string | null;
  created_by: string;
  status: BroadcastJobStatus;
  categories: BroadcastCategory[];
  explicit_telegram_ids_count: number;
  message_text: string;
  parse_mode: string;
  buttons: BroadcastButtonView[];
  bonus: BroadcastBonusConfig | null;
  total_recipients: number;
  sent_count: number;
  failed_count: number;
  blocked_count: number;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
}

/** Кнопка задачи как хранится/возвращается (формат Telegram inline-кнопки). */
export interface BroadcastButtonView {
  text: string;
  callback_data?: string;
  url?: string;
}

/** Курированные действия бота (callback_data) — ключи для выбора в UI. */
export type BroadcastActionButton =
  | "buy_vpn"
  | "main_menu"
  | "enter_promo"
  | "my_subscription"
  | "help";

export const BROADCAST_ACTION_BUTTONS: { key: BroadcastActionButton; label: string }[] = [
  { key: "buy_vpn", label: "🔑 Купить VPN" },
  { key: "main_menu", label: "🏠 Главное меню" },
  { key: "enter_promo", label: "🎟 Ввести промокод" },
  { key: "my_subscription", label: "📋 Моя подписка" },
  { key: "help", label: "❓ Помощь" },
];

export interface BroadcastUrlButton {
  label: string;
  url: string;
}

/** Payload кнопок для send/test. */
export interface BroadcastButtonsPayload {
  action_buttons?: BroadcastActionButton[];
  url_buttons?: BroadcastUrlButton[];
}

/** Бонус-кнопка рассылки (MVP: только бесплатные дни, type="days"; type="promo" зарезервирован
 * под следующий PR). При нажатении получателю начисляются дни подписки. */
export type BroadcastBonusConfig =
  | { type: "days"; days: number; label: string; ttl_days?: number } // дни 3..30
  | { type: "promo"; code: string; label: string; ttl_days?: number }; // авто-применение промокода

export interface BroadcastJobsResponse {
  jobs: BroadcastJob[];
  limit: number;
  offset: number;
}

export interface BroadcastSendResponse {
  job_id: string;
  total_recipients: number;
  explicit_unresolved?: string[];
  bonus_warnings?: string[]; // non-blocking подсказки админу (audience/TTL промокода)
}

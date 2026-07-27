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

export type PromoAudience = "new_users" | "existing_users";
export type PromoDiscountKind = "percent" | "fixed_rubles";

export interface AdminPromocode {
  code: string;
  audience: PromoAudience;
  discount_kind: PromoDiscountKind;
  discount_value: number;
  /** Per-tariff скидки {plan_id: value}; null — плоская мода (discount_value). */
  discounts_by_plan: Record<string, number> | null;
  max_redemptions: number | null;
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
  expires_at?: string | null;
  note?: string | null;
  is_active?: boolean;
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
  };
  by_plan: AdminStatsByPlan[];
}

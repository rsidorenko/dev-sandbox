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
  discount_value: number;
  max_redemptions?: number | null;
  expires_at?: string | null;
  note?: string | null;
  is_active?: boolean;
}

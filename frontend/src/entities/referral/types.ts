/**
 * Типы ответа GET /api/v1/user/referral — реферальный дашборд.
 * Форма соответствует backend: app/web_api/referral.py (handle_get_referral_stats).
 * apiFetch возвращает {ok,data}, где data — весь JSON-ответ, поэтому корневое ok
 * тоже присутствует (на success-пути referral всегда заполнен; ошибки идут через {ok:false}).
 */

export interface ReferralLevelStats {
  invited: number;
  paid: number;
}

export interface ReferralTransaction {
  label: string;
  type: string; // "referral_credit" | "subscription_payment"
  amount_rubles: number;
  level: number | null; // 1 | 2 | null — only referral credits carry a level
  plan_id: string | null;
  created_at: string | null;
}

export interface ReferralTransactionsResponse {
  ok: boolean;
  transactions: ReferralTransaction[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ReferralStats {
  code: string;
  web_link: string;
  telegram_link?: string;
  balance_rubles: number;
  program: {
    invitee_bonus_days: number;
    qualifying_plan_min: string;
    max_level1_percent: number;
    max_level2_percent: number;
  };
  stats: {
    level1: ReferralLevelStats;
    level2: ReferralLevelStats;
    total_earned_rubles: number;
    bonus_days_awarded: number;
    invitee_bonus_days_received: number;
  };
  recent_transactions: ReferralTransaction[];
}

export interface ReferralStatsResponse {
  ok: boolean;
  referral: ReferralStats;
}

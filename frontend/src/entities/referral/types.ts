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
  type: string;
  amount_rubles: number;
  plan_id: string | null;
  description: string | null;
  created_at: string | null;
}

export interface ReferralStats {
  code: string;
  web_link: string;
  telegram_link?: string;
  balance_rubles: number;
  stats: {
    level1: ReferralLevelStats;
    level2: ReferralLevelStats;
    total_earned_rubles: number;
  };
  recent_transactions: ReferralTransaction[];
}

export interface ReferralStatsResponse {
  ok: boolean;
  referral: ReferralStats;
}

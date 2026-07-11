export interface UserProfile {
  user: {
    telegram_user_id: number;
    email: string;
    internal_user_id: string;
  };
  subscription: {
    state: string;
    active_until: string | null;
    remaining_days: number | null;
    plan_id: string | null;
    device_count: number | null;
    // Бэкенд (web_api/profile.py) отдаёт поля триала; нужны для предпроверки
    // на странице /trial, чтобы не дёргать активацию повторно.
    trial_available?: boolean;
    trial_started_at?: string | null;
    trial_expires_at?: string | null;
  } | null;
  keys: { available: boolean; status: string } | null;
  referral: {
    code: string;
    balance_rubles: number;
    referrals_count: number;
    web_referral_link?: string;
  } | null;
}

export interface KeyEntry {
  label: string;
  country: string;
  flag: string;
  link: string;
}

export interface KeysResponse {
  ok: boolean;
  keys: KeyEntry[];
  subscription_url: string | null;
}

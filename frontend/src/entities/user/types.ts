export interface UserProfile {
  user: {
    telegram_user_id: number;
    pending?: boolean;
    expires_at?: string;
    // link-auth: у токен-сессий почты нет; поле остаётся для legacy-JWT перехода.
    email: string | null;
    internal_user_id: string;
    /** Ссылка входа {site}/auth/<token> — единственный креденшл, показываем в ЛК. */
    login_url?: string;
    /** Ссылка подписки {site}/sub/<token> (VPN-конфиги). */
    subscription_url?: string;
  };
  subscription: {
    state: string;
    active_until: string | null;
    remaining_days: number | null;
    plan_id: string | null;
    device_count: number | null;
    // Поля триала отражают состояние (использован/даты); активация триала —
    // только в Telegram-боте, страница /trial больше не предпроверяет.
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

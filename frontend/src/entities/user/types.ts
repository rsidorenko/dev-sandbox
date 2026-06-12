export interface UserProfile {
  user: {
    telegram_user_id: number;
    email: string;
    internal_user_id: string;
  };
  subscription: {
    state: string;
    active_until: string | null;
    plan_id: string | null;
    device_count: number | null;
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

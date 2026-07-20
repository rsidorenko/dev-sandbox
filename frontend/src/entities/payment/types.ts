export interface PaymentCreateResponse {
  ok: boolean;
  status: string;
  plan_id: string;
  plan_name: string;
  device_count: number;
  amount_rubles: number;
  amount_kopecks: number;
  message?: string;
  payment_url?: string;
  payment_id?: string;
}

export interface AddDevicesResponse {
  ok: boolean;
  status: string;
  device_count: number;
  amount_rubles: number;
  amount_kopecks: number;
  payment_url?: string;
  payment_id?: string;
}

/**
 * Pay from the referral balance (web equivalent of the bot's balance-payment).
 * status: completed | already_completed (idempotent replay of X-Idempotency-Key) |
 * insufficient_balance | payment_unavailable | vless_creation_failed | payment_in_progress.
 */
export interface BalancePaymentResponse {
  ok: boolean;
  status:
    | "completed"
    | "already_completed"
    | "insufficient_balance"
    | "payment_unavailable"
    | "vless_creation_failed"
    | "payment_in_progress";
  plan_id?: string;
  plan_name?: string;
  device_count?: number;
  amount_rubles?: number;
  amount_kopecks?: number;
  active_until?: string;
  balance_rubles_remaining?: number;
  message?: string;
}

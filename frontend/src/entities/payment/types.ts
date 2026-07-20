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
 *
 * Only `completed` / `already_completed` arrive here as data.status (HTTP 200, ok:true).
 * Recoverable failure codes — `insufficient_balance`, `payment_in_progress`,
 * `vless_creation_failed`, `balance_payment_failed`, `no_active_subscription`,
 * `device_count_not_higher` — arrive via the apiFetch error channel as `result.error`
 * (backend `safe_json_error` → HTTP 4xx/5xx, `{ok:false, error:<code>}`), NOT as
 * data.status. Frontend handlers must inspect `result.error` in the `!ok` branch.
 */
export interface BalancePaymentResponse {
  ok: boolean;
  status: "completed" | "already_completed";
  plan_id?: string;
  plan_name?: string;
  device_count?: number;
  amount_rubles?: number;
  amount_kopecks?: number;
  active_until?: string;
  balance_rubles_remaining?: number;
  message?: string;
}

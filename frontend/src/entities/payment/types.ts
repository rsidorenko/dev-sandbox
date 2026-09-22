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

/** Принятая оплата может ждать автоматической выдачи доступа (HTTP 202). */
export interface BalancePaymentResponse {
  ok: boolean;
  status: "completed" | "already_completed" | "provisioning";
  plan_id?: string;
  plan_name?: string;
  device_count?: number;
  amount_rubles?: number;
  amount_kopecks?: number;
  active_until?: string;
  balance_rubles_remaining?: number;
  message?: string;
}

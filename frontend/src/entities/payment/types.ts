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
  promo_code?: string;
  discount_percent?: number;
  original_rubles?: number;
  discounted_rubles?: number;
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

export interface PromoValidateResponse {
  ok: boolean;
  valid: boolean;
  reason?: string;
  discount_percent?: number;
  original_rubles?: number;
  discounted_rubles?: number;
}

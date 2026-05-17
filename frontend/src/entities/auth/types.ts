export interface SendCodeResponse {
  ok: boolean;
  sent: boolean;
  ttl_minutes: number;
}

export interface VerifyResponse {
  ok: boolean;
  token: string;
  user: { telegram_user_id: number; email: string };
}

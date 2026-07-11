import { apiFetch } from "@/shared/api/client";

/** Результат активации пробного периода (POST /api/v1/user/trial/activate). */
export interface TrialServer {
  label?: string;
  country_code?: string;
  country_flag?: string;
  vless_link: string;
}

export interface TrialActivation {
  trial: { started_at: string; expires_at: string };
  keys: {
    subscription_url: string;
    servers: TrialServer[];
  };
}

export const trialApi = {
  /** Активировать 3-дневный пробный период. Создаёт реальные VLESS-ключи. */
  activate: () =>
    apiFetch<TrialActivation>("/api/v1/user/trial/activate", {
      method: "POST",
    }),
};

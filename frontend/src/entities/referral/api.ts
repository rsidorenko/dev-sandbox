import { apiFetch } from "@/shared/api/client";
import type { ReferralStatsResponse } from "./types";

export const referralApi = {
  /** GET /api/v1/user/referral — код, ссылки, баланс, L1/L2 + конверсия, всего заработано, история. */
  getStats: () => apiFetch<ReferralStatsResponse>("/api/v1/user/referral"),
};

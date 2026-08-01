import { apiFetch } from "@/shared/api/client";
import type { ReferralStatsResponse, ReferralTransactionsResponse } from "./types";

export type ReferralTxType = "credit" | "debit";
export type ReferralTxLevel = 1 | 2;

export const referralApi = {
  /** GET /api/v1/user/referral — код, ссылки, баланс, L1/L2 + конверсия, всего заработано. */
  getStats: () => apiFetch<ReferralStatsResponse>("/api/v1/user/referral"),

  /** GET /api/v1/user/referral/transactions — расширенная, отфильтрованная история
   * начислений. Возвращает только чистые поля (label/type/amount/level/plan/created_at),
   * никогда — внутренние description/idempotency_key. */
  getTransactions: (params?: {
    level?: ReferralTxLevel;
    type?: ReferralTxType;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.level) qs.set("level", String(params.level));
    if (params?.type) qs.set("type", params.type);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const query = qs.toString();
    return apiFetch<ReferralTransactionsResponse>(
      `/api/v1/user/referral/transactions${query ? `?${query}` : ""}`,
    );
  },
};

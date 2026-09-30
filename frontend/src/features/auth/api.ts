import { apiFetch } from "@/shared/api/client";
import type { LinkRequestResponse } from "@/entities/auth/types";
export const authApi = {
  requestLink: (referral_code?: string) => apiFetch<LinkRequestResponse>("/api/v1/auth/link/request", {
    method: "POST", body: JSON.stringify(referral_code ? { referral_code } : {}),
  }),
};

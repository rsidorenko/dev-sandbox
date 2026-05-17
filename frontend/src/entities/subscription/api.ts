import { apiFetch } from "@/shared/api/client";
import type { SubscriptionActionResponse } from "./types";

export const subscriptionApi = {
  renew: () =>
    apiFetch<SubscriptionActionResponse>(
      "/api/v1/user/subscription/renew",
      { method: "POST" },
    ),
  changePlan: (planId: string) =>
    apiFetch<SubscriptionActionResponse>(
      "/api/v1/user/subscription/change-plan",
      { method: "POST", body: JSON.stringify({ plan_id: planId }) },
    ),
  changeDevices: (count: number) =>
    apiFetch<SubscriptionActionResponse>(
      "/api/v1/user/subscription/change-devices",
      { method: "POST", body: JSON.stringify({ device_count: count }) },
    ),
  cancel: () =>
    apiFetch<SubscriptionActionResponse>(
      "/api/v1/user/subscription/cancel",
      { method: "POST" },
    ),
};

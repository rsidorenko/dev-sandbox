import { apiFetch } from "@/shared/api/client";
import type { PaymentCreateResponse } from "./types";

export const paymentApi = {
  create: (planId: string, deviceCount = 5) =>
    apiFetch<PaymentCreateResponse>("/api/v1/payment/create", {
      method: "POST",
      body: JSON.stringify({ plan_id: planId, device_count: deviceCount }),
    }),
};

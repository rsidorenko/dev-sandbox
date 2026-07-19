import { apiFetch } from "@/shared/api/client";
import type { AddDevicesResponse, PaymentCreateResponse, PromoValidateResponse } from "./types";

export const paymentApi = {
  create: (planId: string, deviceCount = 5, promoCode?: string) =>
    apiFetch<PaymentCreateResponse>("/api/v1/payment/create", {
      method: "POST",
      body: JSON.stringify({
        plan_id: planId,
        device_count: deviceCount,
        ...(promoCode ? { promo_code: promoCode } : {}),
      }),
    }),
  // Add devices to an existing active subscription (kind=add_device top-up —
  // charges only for the extra devices, does NOT renew the subscription).
  addDevices: (deviceCount: number) =>
    apiFetch<AddDevicesResponse>("/api/v1/payment/add-devices", {
      method: "POST",
      body: JSON.stringify({ device_count: deviceCount }),
    }),
  // Preview a promo discount (read-only; the code is consumed at payment creation).
  validatePromo: (code: string, planId: string, deviceCount = 5) =>
    apiFetch<PromoValidateResponse>("/api/v1/promo/validate", {
      method: "POST",
      body: JSON.stringify({ code, plan_id: planId, device_count: deviceCount }),
    }),
};

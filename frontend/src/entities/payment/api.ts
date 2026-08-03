import { apiFetch } from "@/shared/api/client";
import type { AddDevicesResponse, BalancePaymentResponse, PaymentCreateResponse } from "./types";

export type PaymentMethod = "card" | "crypto";

export const paymentApi = {
  create: (planId: string, deviceCount = 5, paymentMethod: PaymentMethod = "card") =>
    apiFetch<PaymentCreateResponse>("/api/v1/payment/create", {
      method: "POST",
      body: JSON.stringify({ plan_id: planId, device_count: deviceCount, payment_method: paymentMethod }),
    }),
  // Add devices to an existing active subscription (kind=add_device top-up —
  // charges only for the extra devices, does NOT renew the subscription).
  addDevices: (deviceCount: number, paymentMethod: PaymentMethod = "card") =>
    apiFetch<AddDevicesResponse>("/api/v1/payment/add-devices", {
      method: "POST",
      body: JSON.stringify({ device_count: deviceCount, payment_method: paymentMethod }),
    }),
  // Pay for a subscription from the referral balance (plan purchase / renewal).
  // The idempotency key (one per PaymentForm mount) dedups double-clicks/retries
  // server-side via the X-Idempotency-Key header.
  payWithBalance: (planId: string, deviceCount: number, idempotencyKey: string) =>
    apiFetch<BalancePaymentResponse>("/api/v1/payment/balance", {
      method: "POST",
      headers: { "X-Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ plan_id: planId, device_count: deviceCount }),
    }),
  // Add devices from the referral balance (charges only the extra-device cost,
  // does NOT renew the subscription).
  addDevicesWithBalance: (newDeviceCount: number, idempotencyKey: string) =>
    apiFetch<BalancePaymentResponse>("/api/v1/payment/add-devices/balance", {
      method: "POST",
      headers: { "X-Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ device_count: newDeviceCount }),
    }),
};

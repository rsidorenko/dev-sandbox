import { apiFetch } from "@/shared/api/client";
import type { AddDevicesResponse, PaymentCreateResponse } from "./types";

export const paymentApi = {
  create: (planId: string, deviceCount = 5) =>
    apiFetch<PaymentCreateResponse>("/api/v1/payment/create", {
      method: "POST",
      body: JSON.stringify({ plan_id: planId, device_count: deviceCount }),
    }),
  // Add devices to an existing active subscription (kind=add_device top-up —
  // charges only for the extra devices, does NOT renew the subscription).
  addDevices: (deviceCount: number) =>
    apiFetch<AddDevicesResponse>("/api/v1/payment/add-devices", {
      method: "POST",
      body: JSON.stringify({ device_count: deviceCount }),
    }),
};

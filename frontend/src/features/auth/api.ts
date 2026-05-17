import { apiFetch } from "@/shared/api/client";
import type { SendCodeResponse, VerifyResponse } from "@/entities/auth/types";

export const authApi = {
  sendCode: (email: string) =>
    apiFetch<SendCodeResponse>("/api/v1/auth/email/send-code", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  verifyCode: (email: string, code: string) =>
    apiFetch<VerifyResponse>("/api/v1/auth/email/verify", {
      method: "POST",
      body: JSON.stringify({ email, code }),
    }),
  logout: () =>
    apiFetch<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST" }),
};

import { apiFetch } from "@/shared/api/client";
import type { KeysResponse, UserProfile } from "./types";

export const userApi = {
  profile: () => apiFetch<UserProfile>("/api/v1/user/profile"),
  keys: () => apiFetch<KeysResponse>("/api/v1/user/keys"),
  reissueKeys: () =>
    apiFetch<KeysResponse>("/api/v1/user/keys/reissue", {
      method: "POST",
    }),
};

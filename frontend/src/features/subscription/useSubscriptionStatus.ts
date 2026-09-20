"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { userApi } from "@/entities/user/api";
import type { UserProfile } from "@/entities/user/types";

/**
 * «Живой» статус подписки в ЛК: опрос профиля по каденции (как
 * useNotifications, без SSE/WS). Опрашиваем независимо от AuthProvider,
 * чтобы не мигать глобальным лоадером на каждом тике.
 */
const POLL_INTERVAL_MS = 30_000;

type Subscription = NonNullable<UserProfile["subscription"]>;

export function useSubscriptionStatus(enabled: boolean) {
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const refresh = useCallback(async () => {
    const res = await userApi.profile();
    // Не затираем статус, если пользователь вышел между тиками.
    if (res.ok && enabledRef.current) setSubscription(res.data.subscription);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);

  return { subscription, refresh };
}

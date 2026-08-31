"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  notificationsApi,
  type ClaimStatus,
  type WebNotification,
} from "./api";

/** Каденция опроса ленты (несколько запросов в минуту — как договорились, без SSE/WS). */
const POLL_INTERVAL_MS = 45_000;
const PAGE_SIZE = 20;

export type ClaimOutcome =
  | { ok: true; status: "ok" | "already_claimed"; days?: number; code?: string }
  | { ok: false; error: string };

export function useNotifications(enabled: boolean) {
  const [items, setItems] = useState<WebNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [claimingId, setClaimingId] = useState<number | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const applyList = useCallback((data: {
    items: WebNotification[];
    unread_count: number;
    has_more: boolean;
  }) => {
    setItems(data.items);
    setUnreadCount(data.unread_count);
    setHasMore(data.has_more);
  }, []);

  const refresh = useCallback(async () => {
    const res = await notificationsApi.list(PAGE_SIZE, 0);
    // Не затираем список, если пользователь разлогинился между тиками.
    if (res.ok && enabledRef.current) applyList(res.data);
  }, [applyList]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);

  const loadMore = useCallback(async () => {
    if (loadingMore) return;
    setLoadingMore(true);
    const res = await notificationsApi.list(PAGE_SIZE, items.length);
    if (res.ok && enabledRef.current) {
      // Сервер может прислать пересечение с уже загруженными (новые сверху) — дедуп по id.
      const seen = new Set(items.map((n) => n.id));
      const fresh = res.data.items.filter((n) => !seen.has(n.id));
      setItems((prev) => [...prev, ...fresh]);
      setHasMore(res.data.has_more);
      setUnreadCount(res.data.unread_count);
    }
    setLoadingMore(false);
  }, [items, loadingMore]);

  const markAllRead = useCallback(async () => {
    setUnreadCount(0); // оптимистично
    setItems((prev) =>
      prev.map((n) => (n.read_at ? n : { ...n, read_at: new Date().toISOString() })),
    );
    await notificationsApi.readAll();
  }, []);

  const markRead = useCallback(async (id: number) => {
    setItems((prev) =>
      prev.map((n) => (n.id === id && !n.read_at ? { ...n, read_at: new Date().toISOString() } : n)),
    );
    setUnreadCount((prev) => Math.max(0, prev - 1));
    await notificationsApi.read([id]);
  }, []);

  /** Клейм бонуса: при успехе локально ставит claim_status=claimed. */
  const claim = useCallback(async (id: number): Promise<ClaimOutcome> => {
    setClaimingId(id);
    try {
      const res = await notificationsApi.claim(id);
      if (res.ok) {
        setItems((prev) =>
          prev.map((n) => (n.id === id ? { ...n, claim_status: "claimed" as ClaimStatus } : n)),
        );
        return { ok: true, status: res.data.status, days: res.data.days, code: res.data.code };
      }
      // already_claimed тоже закрывает кнопку
      if (res.error === "already_claimed") {
        setItems((prev) =>
          prev.map((n) => (n.id === id ? { ...n, claim_status: "claimed" as ClaimStatus } : n)),
        );
      }
      return { ok: false, error: res.error };
    } finally {
      setClaimingId(null);
    }
  }, []);

  return {
    items,
    unreadCount,
    hasMore,
    loadingMore,
    claimingId,
    refresh,
    loadMore,
    markAllRead,
    markRead,
    claim,
  };
}

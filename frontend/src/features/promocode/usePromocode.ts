"use client";

import { useCallback, useEffect, useState } from "react";
import { promoApi, type AppliedPromo, type PromoPreview } from "./api";

type ApplyResult =
  | { ok: true; data: { applied: AppliedPromo | null; preview?: PromoPreview } }
  | { ok: false; error: string };

/**
 * Состояние закреплённого промокода (TTL 24ч, хранится в БД — переживает деплой).
 * GET на маунте; apply пере-валидирует на сервере (fail-closed) и опц. возвращает
 * preview скидки для конкретного тарифа; clear снимает закрепление.
 */
export function usePromocode() {
  const [applied, setApplied] = useState<AppliedPromo | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const r = await promoApi.get();
    setApplied(r.ok ? r.data.applied : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const apply = useCallback(
    async (code: string, planId?: string, deviceCount?: number): Promise<ApplyResult> => {
      const r = await promoApi.apply(code, planId, deviceCount);
      if (!r.ok) {
        setApplied(null);
        return { ok: false, error: r.error };
      }
      setApplied(r.data.applied);
      return { ok: true, data: r.data };
    },
    [],
  );

  const clear = useCallback(async () => {
    await promoApi.clear();
    setApplied(null);
  }, []);

  return { applied, loading, refresh, apply, clear };
}

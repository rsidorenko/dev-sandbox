"use client";

/**
 * Провайдер каталога тарифов: один fetch /api/v1/plans на маунт сайта.
 *
 * Fallback-политика: пока каталог не загружен (или API недоступен) потребители
 * получают пустые карты и обязаны откатываться на siteConfig.tariffs — витрина
 * всегда рендерится, отображение может временно отличаться от чекаута. Сумму
 * списания считает сервер (/api/v1/payment/create) — расхождение отображения
 * не влияет на деньги.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { fetchPlansCatalog } from "./api";
import type { GlobalDiscountDescriptor } from "@/entities/plans/types";

interface PlansContextValue {
  /** Эффективные цены {plan_id: base_rubles}; пусто → использовать siteConfig. */
  prices: Record<string, number>;
  globalDiscount: GlobalDiscountDescriptor | null;
  loaded: boolean;
}

const PlansContext = createContext<PlansContextValue>({
  prices: {},
  globalDiscount: null,
  loaded: false,
});

export function PlansProvider({ children }: { children: ReactNode }) {
  const [value, setValue] = useState<PlansContextValue>({ prices: {}, globalDiscount: null, loaded: false });

  useEffect(() => {
    let cancelled = false;
    fetchPlansCatalog().then((catalog) => {
      if (cancelled || catalog === null) return;
      const prices: Record<string, number> = {};
      for (const p of catalog.plans) prices[p.plan_id] = p.base_rubles;
      setValue({ prices, globalDiscount: catalog.global_discount, loaded: true });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return <PlansContext.Provider value={value}>{children}</PlansContext.Provider>;
}

export function usePlans() {
  return useContext(PlansContext);
}

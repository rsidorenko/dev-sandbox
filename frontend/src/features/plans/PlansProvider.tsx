"use client";
import { createContext, useContext, useCallback, useEffect, useState, type ReactNode } from "react";
import { fetchPlansCatalog } from "./api";
import type { PlansCatalogResponse } from "@/entities/plans/types";
type Value = { catalog: PlansCatalogResponse | null; prices: Record<string, number>; globalDiscount: PlansCatalogResponse["global_discount"]; loaded: boolean; error: boolean; reload: () => void };
const PlansContext = createContext<Value>({ catalog: null, prices: {}, globalDiscount: null, loaded: false, error: false, reload: () => {} });
export function PlansProvider({ children }: { children: ReactNode }) {
  const [catalog, setCatalog] = useState<PlansCatalogResponse | null>(null);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision(v => v + 1), []);
  useEffect(() => {
    let cancelled = false;
    fetchPlansCatalog().then(value => { if (!cancelled) { setCatalog(value); setError(value === null); } });
    const timer = setTimeout(reload, 30000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [revision, reload]);
  const prices = Object.fromEntries((catalog?.plans ?? []).map(p => [p.plan_id, p.base_rubles]));
  return <PlansContext.Provider value={{ catalog, prices, globalDiscount: catalog?.global_discount ?? null, loaded: catalog !== null, error, reload }}>{children}</PlansContext.Provider>;
}
export function usePlans() { return useContext(PlansContext); }
export function CatalogUnavailable() {
  const { error, reload } = usePlans();
  return <div className="p-6 text-center" role="status"><p>{error ? "Не удалось загрузить актуальные тарифы." : "Загружаем актуальные тарифы…"}</p>{error && <button onClick={reload} className="mt-3 underline">Повторить загрузку</button>}</div>;
}

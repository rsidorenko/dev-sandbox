import type { PlansCatalogResponse } from "@/entities/plans/types";
import { globalDiscountRubles } from "@/features/plans/effectivePrice";
export function getTariff(id: string, catalog?: PlansCatalogResponse | null) { return catalog?.plans.find(p => p.plan_id === id); }
export function priceForPlan(id: string, catalog?: PlansCatalogResponse | null): number {
  const p = getTariff(id, catalog); return p && catalog ? globalDiscountRubles(catalog.global_discount, id, p.base_rubles) : 0;
}
export function perDayRub(id: string, catalog?: PlansCatalogResponse | null): number {
  const p = getTariff(id, catalog); return p ? Math.round(priceForPlan(id, catalog) / p.duration_days) : 0;
}
export function savingsPctForPlan(id: string, catalog?: PlansCatalogResponse | null): number {
  const p = getTariff(id, catalog); const daily = priceForPlan("1d", catalog);
  if (!p || daily <= 0) return 0;
  const pct = Math.round((1 - priceForPlan(id, catalog) / p.duration_days / daily) * 100);
  return pct >= 10 ? pct : 0;
}

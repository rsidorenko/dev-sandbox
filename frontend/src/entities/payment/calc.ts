import { planName } from "@/entities/tariff/helpers";
import type { PlansCatalogResponse } from "@/entities/plans/types";
export const DEFAULT_DEVICES = 5;
export interface TariffInfo { label: string; durationDays: number; basePrice: number; }
/** Округление до ближайшего чётного, как round() в расчёте сервера. */
export function roundRubles(value: number): number {
  const floor = Math.floor(value); return value - floor === 0.5 ? (floor % 2 === 0 ? floor : floor + 1) : Math.round(value);
}
export function calcExtraDeviceCost(durationDays: number, extraDevices: number, monthlyPrice: number): number {
  return extraDevices * roundRubles((monthlyPrice / 30) * durationDays);
}
export function getTariffInfo(planId: string, catalog?: PlansCatalogResponse | null): TariffInfo | null {
  if (!catalog) return null;
  const tariff = catalog.plans.find(t => t.plan_id === planId);
  if (tariff) return { label: planName(planId), durationDays: tariff.duration_days, basePrice: tariff.base_rubles };
  const match = planId.match(/^custom:(\d+)$/);
  if (!match) return null;
  const days = Number(match[1]);
  return days >= 1 && days <= 365 ? { label: planName(planId), durationDays: days, basePrice: days * catalog.custom_day_price_rubles } : null;
}

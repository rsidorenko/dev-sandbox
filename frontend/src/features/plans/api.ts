/** Клиент публичного каталога тарифов (без auth; ответ кэшируется CDN на 30с). */
import type { PlansCatalogResponse } from "@/entities/plans/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export async function fetchPlansCatalog(): Promise<PlansCatalogResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/plans`, { credentials: "omit" });
    if (!res.ok) return null;
    const body = (await res.json()) as PlansCatalogResponse;
    const positive = (n: unknown) => typeof n === "number" && Number.isSafeInteger(n) && n > 0;
    return body.ok && Array.isArray(body.plans) && body.plans.length > 0 &&
      ["1d","7d","14d","1m","3m","6m","365d"].every(id => body.plans.filter(p => p.plan_id === id).length === 1) &&
      body.plans.every(p => typeof p.plan_id === "string" && positive(p.base_rubles) && positive(p.duration_days)) &&
      positive(body.custom_day_price_rubles) && positive(body.extra_device_price_rubles) &&
      positive(body.devices?.base) && positive(body.devices?.max) && body.devices.max >= body.devices.base ? body : null;
  } catch {
    return null;
  }
}

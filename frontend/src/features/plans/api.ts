/** Клиент публичного каталога тарифов (без auth; ответ кэшируется CDN на 30с). */
import type { PlansCatalogResponse } from "@/entities/plans/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function fetchPlansCatalog(): Promise<PlansCatalogResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/plans`, { credentials: "omit" });
    if (!res.ok) return null;
    const body = (await res.json()) as PlansCatalogResponse;
    return body.ok ? body : null;
  } catch {
    return null;
  }
}

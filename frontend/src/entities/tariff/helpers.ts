import { siteConfig } from "@/shared/config/site";

export type Tariff = (typeof siteConfig.tariffs)[number];

export const PLANS = [
  { id: "plan_1m", label: "1 месяц", months: 1 },
  { id: "plan_3m", label: "3 месяца", months: 3 },
  { id: "plan_6m", label: "6 месяцев", months: 6 },
] as const;

export function planName(planId: string | null): string {
  if (!planId) return "";
  const map: Record<string, string> = {
    "1m": "1 месяц",
    "3m": "3 месяца",
    "6m": "6 месяцев",
    plan_1m: "1 месяц",
    plan_3m: "3 месяца",
    plan_6m: "6 месяцев",
  };
  return map[planId] || planId;
}

export function normalizePlanId(
  id: string | null | undefined,
): string {
  if (!id) return "1m";
  return id.replace("plan_", "");
}

export function daysLeft(activeUntil: string): number {
  return Math.max(
    0,
    Math.ceil(
      (new Date(activeUntil).getTime() - Date.now()) / 86400000,
    ),
  );
}

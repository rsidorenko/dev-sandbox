import type { AdminStatsPeriod } from "@/entities/admin/types";

export const ANALYTICS_SECTIONS = { sales: "Продажи", product: "Активация и удержание", ads: "Реклама", vpn: "Качество VPN" } as const;
export function readAnalyticsLocation(params: Pick<URLSearchParams, "get">, today: string) {
  const sectionValue = params.get("section") ?? "sales";
  const section = Object.hasOwn(ANALYTICS_SECTIONS, sectionValue) ? sectionValue as keyof typeof ANALYTICS_SECTIONS : "sales";
  const requested = params.get("period") ?? "7d";
  let period: AdminStatsPeriod = ["1d", "7d", "30d", "90d", "all"].includes(requested) ? requested as AdminStatsPeriod : "7d";
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const validDate = (date: string) => /^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(Date.parse(date)) && new Date(date).toISOString().slice(0, 10) === date;
  let customRange: { dateFrom: string; dateTo: string } | null = null;
  if (requested === "custom" && validDate(from) && validDate(to) && from <= to && to <= today && (Date.parse(to) - Date.parse(from)) / 86400000 < 366) {
    period = "custom"; customRange = { dateFrom: from, dateTo: to };
  }
  return { section, period, customRange };
}

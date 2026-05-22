export const PLANS = [
  { id: "1d", label: "1 день", durationDays: 1 },
  { id: "7d", label: "7 дней", durationDays: 7 },
  { id: "14d", label: "2 недели", durationDays: 14 },
  { id: "1m", label: "1 месяц", durationDays: 30 },
  { id: "3m", label: "3 месяца", durationDays: 90 },
  { id: "6m", label: "6 месяцев", durationDays: 180 },
  { id: "365d", label: "1 год", durationDays: 365 },
] as const;

const PLAN_NAMES: Record<string, string> = {
  "1d": "1 день",
  "7d": "7 дней",
  "14d": "2 недели",
  "1m": "1 месяц",
  "3m": "3 месяца",
  "6m": "6 месяцев",
  "365d": "1 год",
};

export function planName(planId: string | null): string {
  if (!planId) return "";
  if (planId.startsWith("custom:")) {
    const days = parseInt(planId.split(":")[1]);
    if (!isNaN(days)) return formatDays(days);
  }
  return PLAN_NAMES[planId] || planId;
}

export function normalizePlanId(id: string | null | undefined): string {
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

function formatDays(days: number): string {
  if (days === 1) return "1 день";
  if (days % 10 === 1 && days !== 11) return `${days} день`;
  if (days % 10 >= 2 && days % 10 <= 4 && !(days >= 12 && days <= 14))
    return `${days} дня`;
  return `${days} дней`;
}

/** Чистая клиентская группировка пользователей для /admin/users (секции «Group by»).
 * Бакеты статуса повторяют логику StateBadge + форму active-but-overdue (как в _row_to_user).
 * Пустые бакеты (0 пользователей) опускаются — показываем только присутствующие, в фиксированном порядке. */

import type { AdminUser } from "@/entities/admin/types";

export type UserGrouping = "none" | "status" | "plan" | "balance" | "trial" | "devices";

export interface UserGroup {
  key: string;
  label: string;
  users: AdminUser[];
  sumBalance: number;
}

// Размер группировки -> [bucketKey, label]. Порядок секций зафиксирован тут.
const STATUS_ORDER: { key: StatusBucket; label: string }[] = [
  { key: "active", label: "Активные" },
  { key: "expired", label: "Истёкшие" },
  { key: "cancelled", label: "Cancelled" },
  { key: "none", label: "Без подписки" },
];

const PLAN_ORDER: { key: string; label: string }[] = [
  { key: "1d", label: "1 день" },
  { key: "7d", label: "7 дней" },
  { key: "14d", label: "14 дней" },
  { key: "1m", label: "1 месяц" },
  { key: "3m", label: "3 месяца" },
  { key: "6m", label: "6 месяцев" },
  { key: "365d", label: "1 год" },
  { key: "none", label: "Без тарифа" },
];

const BALANCE_ORDER: { key: BalanceBucket; label: string }[] = [
  { key: "0", label: "0 ₽" },
  { key: "1-99", label: "1–99 ₽" },
  { key: "100+", label: "100+ ₽" },
];

const TRIAL_ORDER: { key: TrialBucket; label: string }[] = [
  { key: "used", label: "Использовал" },
  { key: "not", label: "Не использовал" },
];

const DEVICES_ORDER: { key: string; label: string }[] = [
  { key: "1", label: "1 устройство" },
  { key: "2", label: "2 устройства" },
  { key: "3", label: "3 устройства" },
  { key: "4", label: "4 устройства" },
  { key: "5+", label: "5+ устройств" },
  { key: "none", label: "Нет данных" },
];

export type StatusBucket = "active" | "expired" | "cancelled" | "none";
export type BalanceBucket = "0" | "1-99" | "100+";
export type TrialBucket = "used" | "not";

/** Бакет статуса: is_active → активный; state 'active' с просрочкой или 'expired' → истёкший;
 * 'cancelled' → отменён; null/нет снапшота → без подписки. Зеркало StateBadge + _row_to_user. */
export function statusBucket(u: AdminUser): StatusBucket {
  if (u.is_active) return "active";
  const s = u.subscription_state;
  if (s === "active" || s === "expired") return "expired"; // active-but-overdue или явный expired
  if (s === "cancelled") return "cancelled";
  return "none";
}

export function balanceBucket(u: AdminUser): BalanceBucket {
  const b = u.balance_rubles;
  if (b <= 0) return "0";
  return b < 100 ? "1-99" : "100+";
}

export function trialBucket(u: AdminUser): TrialBucket {
  return u.trial_used ? "used" : "not";
}

function planBucket(u: AdminUser): string {
  return u.plan_id ?? "none";
}

function devicesBucket(u: AdminUser): string {
  const d = u.device_count;
  if (d == null) return "none";
  return d >= 5 ? "5+" : String(d);
}

/** Группирует пользователей по выбранному измерению, возвращает секции в фиксированном
 * порядке, пропуская пустые (0 пользователей). sumBalance — сумма balance_rubles в секции. */
export function groupUsers(users: AdminUser[], dim: UserGrouping): UserGroup[] {
  if (dim === "none") return [];
  const bucketOf: (u: AdminUser) => string =
    dim === "status" ? statusBucket : dim === "balance" ? balanceBucket : dim === "trial" ? trialBucket : dim === "plan" ? planBucket : devicesBucket;
  const order =
    dim === "status" ? STATUS_ORDER : dim === "plan" ? PLAN_ORDER : dim === "balance" ? BALANCE_ORDER : dim === "trial" ? TRIAL_ORDER : DEVICES_ORDER;

  const byKey = new Map<string, AdminUser[]>();
  for (const u of users) {
    const k = bucketOf(u);
    const arr = byKey.get(k);
    if (arr) arr.push(u);
    else byKey.set(k, [u]);
  }

  const groups: UserGroup[] = [];
  for (const { key, label } of order) {
    const arr = byKey.get(key);
    if (!arr || arr.length === 0) continue;
    groups.push({
      key,
      label,
      users: arr,
      sumBalance: Math.round(arr.reduce((s, u) => s + u.balance_rubles, 0) * 100) / 100,
    });
  }
  return groups;
}

export const GROUPING_OPTIONS: { value: UserGrouping; label: string }[] = [
  { value: "none", label: "Без группы" },
  { value: "status", label: "По статусу" },
  { value: "plan", label: "По тарифу" },
  { value: "balance", label: "По балансу" },
  { value: "trial", label: "По триалу" },
  { value: "devices", label: "По числу устройств" },
];

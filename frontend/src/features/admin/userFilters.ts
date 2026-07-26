/** Фасетные фильтры пользователей для /admin/users (как на e-commerce: выбираешь
 * конкретные значения по нескольким измерениям, список сужается).
 * Бакеты статуса повторяют StateBadge + форму active-but-overdue (как в _row_to_user).
 * Чистые функции — клиентская фильтрация по полной загрузке (cap _MAX_LIMIT=1000 на бэке). */

import type { AdminUser } from "@/entities/admin/types";

export type FacetName = "status" | "plan" | "balance" | "trial" | "devices";
export type Filters = Record<FacetName, Set<string>>;
export const FACET_NAMES: FacetName[] = ["status", "plan", "balance", "trial", "devices"];

export const FACET_TITLES: Record<FacetName, string> = {
  status: "Статус подписки",
  plan: "Тариф",
  balance: "Баланс",
  trial: "Триал",
  devices: "Устройства",
};

// Значения фасета в фиксированном порядке + подписи.
export const FACET_VALUES: Record<FacetName, { key: string; label: string }[]> = {
  status: [
    { key: "active", label: "Активные" },
    { key: "expired", label: "Истёкшие" },
    { key: "cancelled", label: "Cancelled" },
    { key: "none", label: "Без подписки" },
  ],
  plan: [
    { key: "1d", label: "1 день" },
    { key: "7d", label: "7 дней" },
    { key: "14d", label: "14 дней" },
    { key: "1m", label: "1 месяц" },
    { key: "3m", label: "3 месяца" },
    { key: "6m", label: "6 месяцев" },
    { key: "365d", label: "1 год" },
    { key: "none", label: "Без тарифа" },
  ],
  balance: [
    { key: "0", label: "0 ₽" },
    { key: "1-99", label: "1–99 ₽" },
    { key: "100+", label: "100+ ₽" },
  ],
  trial: [
    { key: "used", label: "Использовал" },
    { key: "not", label: "Не использовал" },
  ],
  devices: [
    { key: "1", label: "1 устройство" },
    { key: "2", label: "2 устройства" },
    { key: "3", label: "3 устройства" },
    { key: "4", label: "4 устройства" },
    { key: "5+", label: "5+ устройств" },
    { key: "none", label: "Нет данных" },
  ],
};

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

export function planBucket(u: AdminUser): string {
  return u.plan_id ?? "none";
}

export function devicesBucket(u: AdminUser): string {
  const d = u.device_count;
  if (d == null) return "none";
  return d >= 5 ? "5+" : String(d);
}

/** Ключ бакета пользователя по измерению. */
export function facetValue(u: AdminUser, facet: FacetName): string {
  switch (facet) {
    case "status":
      return statusBucket(u);
    case "plan":
      return planBucket(u);
    case "balance":
      return balanceBucket(u);
    case "trial":
      return trialBucket(u);
    case "devices":
      return devicesBucket(u);
  }
}

/** Подпись значения фасета (из фиксированного списка, иначе сам ключ). */
export function facetValueLabel(facet: FacetName, key: string): string {
  return FACET_VALUES[facet].find((v) => v.key === key)?.label ?? key;
}

export function emptyFilters(): Filters {
  return { status: new Set(), plan: new Set(), balance: new Set(), trial: new Set(), devices: new Set() };
}

export function hasFilters(f: Filters): boolean {
  return FACET_NAMES.some((n) => f[n].size > 0);
}

/** Пользователь проходит, если по КАЖДОЙ активной фасете его бакет в выборе (AND между
 * фасетами, OR внутри фасеты). Пустая фасета — не фильтрует. */
export function matchesFilters(u: AdminUser, f: Filters): boolean {
  return FACET_NAMES.every((n) => f[n].size === 0 || f[n].has(facetValue(u, n)));
}

export function filterUsers(users: AdminUser[], f: Filters): AdminUser[] {
  return f.status.size || f.plan.size || f.balance.size || f.trial.size || f.devices.size
    ? users.filter((u) => matchesFilters(u, f))
    : users;
}

export interface FacetOption {
  key: string;
  label: string;
  count: number;
}

/** Счётчики по каждой фасете с учётом ОСТАЛЬНЫХ активных фильтров (faceted search):
 * выбор в одной фасете обновляет счётчики в других. Возвращает только непустые значения
 * в фиксированном порядке. */
export function computeFacets(users: AdminUser[], f: Filters): Record<FacetName, FacetOption[]> {
  const res = {} as Record<FacetName, FacetOption[]>;
  for (const facet of FACET_NAMES) {
    // подмножество, отфильтрованное ВСЕМИ фасетами кроме текущей
    const subset = users.filter((u) =>
      FACET_NAMES.every((g) => g === facet || f[g].size === 0 || f[g].has(facetValue(u, g))),
    );
    const counts = new Map<string, number>();
    for (const u of subset) {
      const k = facetValue(u, facet);
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    res[facet] = FACET_VALUES[facet].filter((v) => counts.has(v.key)).map((v) => ({ ...v, count: counts.get(v.key)! }));
  }
  return res;
}

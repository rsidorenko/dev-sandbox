/**
 * Расчёт «выгоды» тарифов — единый для лендинга и личного кабинета.
 *
 * Эталон — самый дорогой в пересчёте на день тариф (1 день).
 * Источник цен: siteConfig.tariffs (id совпадают с PLANS в ./helpers.ts).
 */
import { siteConfig } from "@/shared/config/site";
import type { Tariff } from "@/shared/config/site";

const DAY1_PRICE = siteConfig.tariffs[0]?.price ?? 12;

/** Найти конфиг тарифа по id (1d, 7d, …, 365d). */
export function getTariff(id: string): Tariff | undefined {
  return siteConfig.tariffs.find((t) => t.id === id);
}

/** Цена тарифа в рублях (0, если id неизвестен). */
export function priceForPlan(id: string): number {
  return getTariff(id)?.price ?? 0;
}

/** Стоимость одного дня для тарифа (₽), с округлением. */
export function perDayRub(id: string): number {
  const t = getTariff(id);
  if (!t || t.durationDays <= 0) return 0;
  return Math.round(t.price / t.durationDays);
}

/** Процент выгоды относительно однодневного тарифа (≥10, иначе 0 — не показываем). */
export function savingsPctForPlan(id: string): number {
  const t = getTariff(id);
  if (!t || t.durationDays <= 0) return 0;
  const perDay = t.price / t.durationDays;
  const pct = Math.round((1 - perDay / DAY1_PRICE) * 100);
  return pct >= 10 ? pct : 0;
}

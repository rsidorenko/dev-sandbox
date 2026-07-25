import type { AppliedPromo } from "./api";

/**
 * Скидочная цена для ОТОБРАЖЕНИЯ — фронт-реплика бэкенд-`apply_promo_discount`
 * (authoritative расчёт живёт на бэке и применяется на checkout; здесь — только
 * показ в списке тарифов). Расхождение возможно на ±1 рубль в edge-case округления,
 * checkout всё равно покажет точную сумму.
 *
 * - applied=null/!valid → base (нет скидки).
 * - per-tariff (discounts_by_plan непуст): custom:* или тариф без записи в map → base.
 * - flat (discount_value): percent → round(base*(100-v)/100); fixed → base-v; clamp [1, base].
 */
export function discountedRubles(
  applied: AppliedPromo | null,
  planId: string,
  baseRubles: number,
): number {
  if (!applied || !applied.valid || baseRubles <= 0) return baseRubles;

  let value: number | null;
  if (applied.discounts_by_plan && Object.keys(applied.discounts_by_plan).length > 0) {
    if (planId.startsWith("custom:")) return baseRubles;
    value = applied.discounts_by_plan[planId] ?? null;
  } else {
    value = applied.discount_value;
  }
  if (value == null || value <= 0) return baseRubles;

  const discounted =
    applied.discount_kind === "percent"
      ? Math.round((baseRubles * (100 - value)) / 100)
      : baseRubles - value;
  return Math.max(1, Math.min(discounted, baseRubles));
}

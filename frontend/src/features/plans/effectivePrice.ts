/**
 * Best-of(промокод, глобальная скидка) — фронт-реплика бэкенд-`effective_plan_price`
 * (domain/pricing.py). Authoritative расчёт — сервер; здесь только отображение
 * (±1 ₽ возможен на edge-case округления, чекаут покажет точную сумму).
 *
 * - промо-нога — существующий discountedRubles (реплика apply_promo_discount);
 * - глобальная — только 7 фиксированных тарифов (custom:* и прочие — база),
 *   клэмп [1..base], округление как у промо;
 * - при равенстве выигрывает промокод (tie → promo, зеркалит бэкенд).
 */
import { discountedRubles } from "@/features/promocode/discount";
import type { AppliedPromo } from "@/features/promocode/api";
import type { GlobalDiscountDescriptor } from "@/entities/plans/types";

export type DiscountWinner = "none" | "promo" | "global";

export interface EffectivePlanPrice {
  base: number;
  discounted: number;
  winner: DiscountWinner;
}

/** Скидка глобальной акции для тарифа (₽); база — если акция не применима. */
export function globalDiscountRubles(
  gd: GlobalDiscountDescriptor | null,
  planId: string,
  base: number,
): number {
  if (!gd || base <= 0 || planId.startsWith("custom:")) return base;
  const discounted =
    gd.discount_kind === "percent"
      ? Math.round((base * (100 - gd.discount_value)) / 100)
      : base - gd.discount_value;
  return Math.max(1, Math.min(discounted, base));
}

export function effectivePlanPrice(opts: {
  base: number;
  planId: string;
  applied: AppliedPromo | null;
  globalDiscount: GlobalDiscountDescriptor | null;
}): EffectivePlanPrice {
  const { base, planId, applied, globalDiscount } = opts;
  if (base <= 0) return { base, discounted: base, winner: "none" };

  const promoDiscounted = discountedRubles(applied, planId, base);
  const globalDiscounted = globalDiscountRubles(globalDiscount, planId, base);

  // Промо выигрывает при меньшем ИЛИ равном итоге (tie → promo).
  if (promoDiscounted <= globalDiscounted) {
    return {
      base,
      discounted: promoDiscounted,
      winner: promoDiscounted < base ? "promo" : "none",
    };
  }
  return {
    base,
    discounted: globalDiscounted,
    winner: globalDiscounted < base ? "global" : "none",
  };
}

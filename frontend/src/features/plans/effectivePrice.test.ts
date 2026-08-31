import { describe, expect, it } from "vitest";
import { effectivePlanPrice, globalDiscountRubles } from "./effectivePrice";
import type { AppliedPromo } from "@/features/promocode/api";
import type { GlobalDiscountDescriptor } from "@/entities/plans/types";

function promo(overrides: Partial<AppliedPromo> = {}): AppliedPromo {
  return {
    code: "WELCOME",
    discount_kind: "percent",
    discount_value: 10,
    discounts_by_plan: null,
    expires_at: null,
    remaining_hours: 24,
    valid: true,
    ...overrides,
  } as AppliedPromo;
}

function gd(kind: "percent" | "fixed_rubles", value: number): GlobalDiscountDescriptor {
  return {
    discount_kind: kind,
    discount_value: value,
    label: kind === "percent" ? `−${value}%` : `−${value} ₽`,
    public_label: null,
  };
}

describe("globalDiscountRubles", () => {
  it("неактивная (null) и custom:N — база без изменений", () => {
    expect(globalDiscountRubles(null, "1m", 249)).toBe(249);
    expect(globalDiscountRubles(gd("percent", 20), "custom:45", 675)).toBe(675);
  });

  it("процент: округление и клэмп [1..base]", () => {
    expect(globalDiscountRubles(gd("percent", 20), "1m", 249)).toBe(199);
    expect(globalDiscountRubles(gd("percent", 100), "1m", 249)).toBe(1);
    expect(globalDiscountRubles(gd("percent", 99), "7d", 99)).toBe(1); // 0.99 → клэмп вверх
  });

  it("фикс: base − value, не ниже 1 ₽", () => {
    expect(globalDiscountRubles(gd("fixed_rubles", 50), "1m", 249)).toBe(199);
    expect(globalDiscountRubles(gd("fixed_rubles", 500), "1m", 249)).toBe(1);
  });
});

describe("effectivePlanPrice (best-of, tie → promo)", () => {
  it("без скидок — база, winner none", () => {
    const r = effectivePlanPrice({ base: 249, planId: "1m", applied: null, globalDiscount: null });
    expect(r).toEqual({ base: 249, discounted: 249, winner: "none" });
  });

  it("глобальная больше промо — global", () => {
    const r = effectivePlanPrice({
      base: 249,
      planId: "1m",
      applied: promo({ discount_value: 10 }),
      globalDiscount: gd("percent", 20),
    });
    expect(r.discounted).toBe(199);
    expect(r.winner).toBe("global");
  });

  it("промо больше глобальной — promo", () => {
    const r = effectivePlanPrice({
      base: 249,
      planId: "1m",
      applied: promo({ discount_value: 30 }),
      globalDiscount: gd("percent", 20),
    });
    expect(r.discounted).toBe(174); // Math.round(249×0.7) — leg промо
    expect(r.winner).toBe("promo");
  });

  it("равенство — промокод (tie → promo, зеркалит бэкенд)", () => {
    const r = effectivePlanPrice({
      base: 200,
      planId: "1m",
      applied: promo({ discount_value: 25 }),
      globalDiscount: gd("percent", 25),
    });
    expect(r.discounted).toBe(150);
    expect(r.winner).toBe("promo");
  });

  it("per-tariff промо с 0 на тарифе уступает глобальной", () => {
    const r = effectivePlanPrice({
      base: 249,
      planId: "1m",
      applied: promo({ discounts_by_plan: { "3m": 15 } }),
      globalDiscount: gd("percent", 10),
    });
    expect(r.discounted).toBe(224);
    expect(r.winner).toBe("global");
  });

  it("custom:N — только промо не существует, глобальная не действует", () => {
    const r = effectivePlanPrice({
      base: 675,
      planId: "custom:45",
      applied: null,
      globalDiscount: gd("percent", 30),
    });
    expect(r).toEqual({ base: 675, discounted: 675, winner: "none" });
  });
});

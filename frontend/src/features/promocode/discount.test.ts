import { describe, expect, it } from "vitest";
import { discountedRubles } from "./discount";
import type { AppliedPromo } from "./api";

function applied(over: Partial<AppliedPromo> = {}): AppliedPromo {
  return {
    code: "P",
    discount_kind: "percent",
    discount_value: 15,
    discounts_by_plan: null,
    discount_label: "−15%",
    expires_at: "2099-01-01T00:00:00Z",
    remaining_hours: 24,
    valid: true,
    ...over,
  };
}

describe("discountedRubles", () => {
  it("no applied / invalid → base", () => {
    expect(discountedRubles(null, "1m", 249)).toBe(249);
    expect(discountedRubles(applied({ valid: false }), "1m", 249)).toBe(249);
  });

  it("flat percent rounds half-up", () => {
    // 249 * 0.85 = 211.65 → 212
    expect(discountedRubles(applied({ discount_kind: "percent", discount_value: 15 }), "1m", 249)).toBe(212);
  });

  it("flat fixed rubles", () => {
    expect(discountedRubles(applied({ discount_kind: "fixed_rubles", discount_value: 50 }), "1m", 249)).toBe(199);
  });

  it("flat clamps to [1, base]", () => {
    expect(discountedRubles(applied({ discount_kind: "fixed_rubles", discount_value: 999 }), "1m", 249)).toBe(1);
    expect(discountedRubles(applied({ discount_kind: "percent", discount_value: 100 }), "1m", 249)).toBe(1);
  });

  it("per-tariff: configured plan gets discount, others base", () => {
    const a = applied({ discounts_by_plan: { "1m": 10, "3m": 50 } });
    expect(discountedRubles(a, "1m", 249)).toBe(224); // −10%
    expect(discountedRubles(a, "3m", 699)).toBe(350); // −50%
    expect(discountedRubles(a, "1d", 12)).toBe(12); // не в map → base
  });

  it("per-tariff: custom tariff → base", () => {
    const a = applied({ discounts_by_plan: { "1m": 50 } });
    expect(discountedRubles(a, "custom:20", 300)).toBe(300);
  });

  it("per-tariff fixed kind", () => {
    const a = applied({ discount_kind: "fixed_rubles", discounts_by_plan: { "1m": 50 } });
    expect(discountedRubles(a, "1m", 249)).toBe(199);
  });
});

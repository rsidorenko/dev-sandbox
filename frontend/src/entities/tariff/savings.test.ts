import { describe, expect, it } from "vitest";
import {
  getTariff,
  perDayRub,
  priceForPlan,
  savingsPctForPlan,
} from "./savings";

describe("getTariff", () => {
  it("finds a tariff by id", () => {
    expect(getTariff("1m")?.price).toBe(249);
  });

  it("returns undefined for unknown id", () => {
    expect(getTariff("nope")).toBeUndefined();
  });
});

describe("priceForPlan", () => {
  it("returns the price for a known plan", () => {
    expect(priceForPlan("365d")).toBe(2199);
  });

  it("returns 0 for an unknown plan", () => {
    expect(priceForPlan("nope")).toBe(0);
  });
});

describe("perDayRub", () => {
  it("rounds price / durationDays", () => {
    // 99 / 7 = 14.14 → 14
    expect(perDayRub("7d")).toBe(14);
  });

  it("returns 0 for unknown plan", () => {
    expect(perDayRub("nope")).toBe(0);
  });
});

describe("savingsPctForPlan", () => {
  // Эталон — 1-дневный тариф (12 ₽/день). Порог отображения: ≥ 10%.
  it.each([
    ["1d", 0],
    ["7d", 0],
    ["14d", 0],
    ["1m", 31],
    ["3m", 35],
    ["6m", 42],
    ["365d", 50],
  ])("%s → %d%%", (id, pct) => {
    expect(savingsPctForPlan(id)).toBe(pct);
  });

  it("returns 0 for unknown plan", () => {
    expect(savingsPctForPlan("nope")).toBe(0);
  });
});

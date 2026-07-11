import { describe, expect, it } from "vitest";
import { PLANS } from "@/entities/tariff/helpers";
import { siteConfig } from "./site";

describe("siteConfig tariffs (data invariants)", () => {
  const tariffs = siteConfig.tariffs;

  it("all ids are unique", () => {
    const ids = tariffs.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every tariff has positive duration, price and string labels", () => {
    for (const t of tariffs) {
      expect(t.durationDays).toBeGreaterThan(0);
      expect(t.price).toBeGreaterThan(0);
      expect(typeof t.priceLabel).toBe("string");
      expect(typeof t.perDay).toBe("string");
    }
  });

  it("exactly one tariff is marked popular", () => {
    expect(tariffs.filter((t) => t.popular).length).toBe(1);
  });

  it("tariff ids match helpers.PLANS ids", () => {
    expect(tariffs.map((t) => t.id)).toEqual(PLANS.map((p) => p.id));
  });

  it("business parameters are set", () => {
    expect(siteConfig.trialDays).toBeGreaterThan(0);
    expect(siteConfig.refundDays).toBeGreaterThan(0);
    expect(siteConfig.customPricePerDay).toBeGreaterThan(0);
    expect(siteConfig.botUsername).toBeTruthy();
    expect(siteConfig.siteUrl).toMatch(/^https:\/\//);
  });
});

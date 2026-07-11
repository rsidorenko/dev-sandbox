import { describe, expect, it } from "vitest";
import { daysLeft, normalizePlanId, planName } from "./helpers";

describe("planName", () => {
  it("returns empty string for null", () => {
    expect(planName(null)).toBe("");
  });

  it("maps known ids to labels", () => {
    expect(planName("1d")).toBe("1 день");
    expect(planName("1m")).toBe("1 месяц");
    expect(planName("365d")).toBe("1 год");
  });

  it("parses custom:N with correct Russian pluralization", () => {
    expect(planName("custom:1")).toBe("1 день");
    expect(planName("custom:2")).toBe("2 дня");
    expect(planName("custom:3")).toBe("3 дня");
    expect(planName("custom:4")).toBe("4 дня");
    expect(planName("custom:5")).toBe("5 дней");
    expect(planName("custom:11")).toBe("11 дней");
    expect(planName("custom:12")).toBe("12 дней");
    expect(planName("custom:14")).toBe("14 дней");
    expect(planName("custom:21")).toBe("21 день");
    expect(planName("custom:22")).toBe("22 дня");
    expect(planName("custom:25")).toBe("25 дней");
  });

  it("returns the id as-is for unknown ids", () => {
    expect(planName("nope")).toBe("nope");
  });
});

describe("normalizePlanId", () => {
  it("defaults to 1m for null/undefined", () => {
    expect(normalizePlanId(null)).toBe("1m");
    expect(normalizePlanId(undefined)).toBe("1m");
  });

  it("strips the plan_ prefix", () => {
    expect(normalizePlanId("plan_1m")).toBe("1m");
    expect(normalizePlanId("plan_365d")).toBe("365d");
  });

  it("passes through normal ids unchanged", () => {
    expect(normalizePlanId("1m")).toBe("1m");
  });
});

describe("daysLeft", () => {
  const NOW = new Date("2026-07-12T12:00:00Z").getTime();

  it("returns ceil of full days until expiry", () => {
    const until = new Date(NOW + 2.5 * 86_400_000).toISOString();
    expect(daysLeft(until, NOW)).toBe(3);
  });

  it("returns 0 for a past date", () => {
    const until = new Date(NOW - 86_400_000).toISOString();
    expect(daysLeft(until, NOW)).toBe(0);
  });

  it("returns 0 when expiry is exactly now", () => {
    const until = new Date(NOW).toISOString();
    expect(daysLeft(until, NOW)).toBe(0);
  });
});

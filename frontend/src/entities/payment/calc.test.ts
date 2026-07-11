import { describe, expect, it } from "vitest";
import {
  CUSTOM_PRICE_PER_DAY,
  DEFAULT_DEVICES,
  EXTRA_DEVICE_PRICE,
  calcExtraDeviceCost,
  getTariffInfo,
} from "./calc";

describe("constants", () => {
  it("DEFAULT_DEVICES is 5", () => {
    expect(DEFAULT_DEVICES).toBe(5);
  });

  it("EXTRA_DEVICE_PRICE is 80", () => {
    expect(EXTRA_DEVICE_PRICE).toBe(80);
  });

  it("CUSTOM_PRICE_PER_DAY mirrors siteConfig", () => {
    expect(CUSTOM_PRICE_PER_DAY).toBe(15);
  });
});

describe("calcExtraDeviceCost", () => {
  it("returns 0 when there are no extra devices", () => {
    expect(calcExtraDeviceCost(30, 0)).toBe(0);
  });

  it("charges per-device proportional to duration", () => {
    // 30 дней (1 месяц): round(80/30*30) = 80 за каждое доп. устройство.
    expect(calcExtraDeviceCost(30, 1)).toBe(80);
    expect(calcExtraDeviceCost(30, 2)).toBe(160);
    // 14 дней: round(80/30*14) = round(37.33) = 37.
    expect(calcExtraDeviceCost(14, 2)).toBe(74);
  });
});

describe("getTariffInfo", () => {
  it("resolves a fixed plan from siteConfig", () => {
    expect(getTariffInfo("1m")).toEqual({
      label: "1 месяц",
      durationDays: 30,
      basePrice: 249,
    });
  });

  it("resolves custom:N as days × CUSTOM_PRICE_PER_DAY", () => {
    expect(getTariffInfo("custom:10")).toEqual({
      label: "10 дней",
      durationDays: 10,
      basePrice: 150,
    });
  });

  it("pluralizes custom:1 correctly", () => {
    expect(getTariffInfo("custom:1")?.label).toBe("1 день");
  });

  it("returns null for an unknown plan", () => {
    expect(getTariffInfo("nope")).toBeNull();
  });
});

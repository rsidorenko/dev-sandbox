import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  YM_ID,
  metrikaInitScript,
  metrikaPixelUrl,
  reachGoal,
} from "./analytics";

beforeEach(() => {
  delete (window as unknown as { ym?: unknown }).ym;
});

afterEach(() => {
  delete (window as unknown as { ym?: unknown }).ym;
});

describe("reachGoal", () => {
  it("is a no-op when window.ym is absent", () => {
    expect(() => reachGoal("purchase")).not.toThrow();
  });

  it("calls window.ym with numeric id and goal", () => {
    const ym = vi.fn();
    (window as unknown as { ym: unknown }).ym = ym;
    reachGoal("purchase", { x: 1 });
    expect(ym).toHaveBeenCalledWith(
      Number(YM_ID),
      "reachGoal",
      "purchase",
      { x: 1 },
    );
  });

  it("swallows errors thrown by window.ym", () => {
    (window as unknown as { ym: () => void }).ym = () => {
      throw new Error("boom");
    };
    expect(() => reachGoal("purchase")).not.toThrow();
  });
});

describe("metrika URLs", () => {
  it("pixel URL contains the YM id", () => {
    expect(metrikaPixelUrl()).toContain(`/watch/${YM_ID}`);
  });

  it("init script contains YM id and the tag.js source", () => {
    const script = metrikaInitScript();
    expect(script).toContain(`id=${YM_ID}`);
    expect(script).toContain("mc.yandex.ru/metrika/tag.js");
  });
});

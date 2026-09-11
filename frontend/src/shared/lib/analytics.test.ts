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
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("Метрика по умолчанию выключена (PD-аудит 2026-09-11)", () => {
  it("YM_ID пуст без NEXT_PUBLIC_YM_ID", () => {
    expect(YM_ID).toBe("");
  });

  it("init-сниппет и пиксель — пустые строки (ничего не рендерится)", () => {
    expect(metrikaInitScript()).toBe("");
    expect(metrikaPixelUrl()).toBe("");
  });
});

describe("Метрика включается только явным NEXT_PUBLIC_YM_ID", () => {
  it("init-сниппет содержит id и tag.js, webvisor выключен", async () => {
    vi.stubEnv("NEXT_PUBLIC_YM_ID", "123456789");
    vi.resetModules();
    const mod = await import("./analytics");
    expect(mod.YM_ID).toBe("123456789");
    const script = mod.metrikaInitScript();
    expect(script).toContain("id=123456789");
    expect(script).toContain("mc.yandex.ru/metrika/tag.js");
    expect(script).toContain("webvisor:false");
    expect(mod.metrikaPixelUrl()).toContain("/watch/123456789");
  });
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

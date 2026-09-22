import { describe, expect, it } from "vitest";
import { readAnalyticsLocation } from "./analyticsLocation";

describe("адрес аналитики", () => {
  it("сохраняет раздел и допустимый диапазон", () => {
    expect(readAnalyticsLocation(new URLSearchParams("section=ads&period=custom&from=2026-09-01&to=2026-09-23"), "2026-09-23")).toEqual({ section: "ads", period: "custom", customRange: { dateFrom: "2026-09-01", dateTo: "2026-09-23" } });
  });
  it.each(["from=2026-02-30&to=2026-03-01", "from=2026-09-23&to=2026-09-01", "from=2026-09-23&to=2026-09-24", "from=2024-01-01&to=2026-09-23"]) ("отклоняет некорректные даты %s", range => {
    expect(readAnalyticsLocation(new URLSearchParams(`period=custom&${range}`), "2026-09-23").period).toBe("7d");
  });
  it("не принимает имена прототипа или неизвестные периоды", () => {
    expect(readAnalyticsLocation(new URLSearchParams("section=constructor&period=tomorrow"), "2026-09-23")).toEqual({ section: "sales", period: "7d", customRange: null });
  });
});

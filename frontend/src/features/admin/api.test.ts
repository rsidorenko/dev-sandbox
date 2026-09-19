import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { adminApi } from "./api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({ ok: true }),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("adminApi.stats", () => {
  it("не добавляет даты к готовому периоду", async () => {
    await adminApi.stats("7d");

    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/api/v1/admin/stats");
    expect(url.searchParams.get("period")).toBe("7d");
    expect(url.searchParams.get("tz")).toBe("Europe/Moscow");
    expect(url.searchParams.has("date_from")).toBe(false);
    expect(url.searchParams.has("date_to")).toBe(false);
  });

  it("передаёт обе даты для произвольного периода", async () => {
    await adminApi.stats("custom", "Europe/Moscow", {
      dateFrom: "2026-09-01",
      dateTo: "2026-09-17",
    });

    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.searchParams.get("period")).toBe("custom");
    expect(url.searchParams.get("date_from")).toBe("2026-09-01");
    expect(url.searchParams.get("date_to")).toBe("2026-09-17");
  });
});

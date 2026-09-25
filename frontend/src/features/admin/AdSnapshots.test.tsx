import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AdSnapshots } from "./AdSnapshots";
import { adminFetch } from "./api";
vi.mock("./api", () => ({ adminFetch: vi.fn() }));

describe("Срез рекламы", () => {
  it("показывает неизвестную дату и исходные валюты отдельно от дневного отчёта", async () => {
    vi.mocked(adminFetch).mockResolvedValue({ ok: true, data: { ok: true, snapshot: {
      source_ref: "test", observed_date: null, created_at: "2026-09-25T10:00:00Z", usd_per_ton: "1.34", notes: "Исходный срез",
      totals: { views: 136970, clicks: 1164, actions: 208, spent_ton: "79.35", spent_usd: "106.33" },
      placements: [{ title: "@androidinsider_ru", views: 641, clicks: 2, actions: 0, cpm_ton: "0.95", spent_ton_lines: ["0.60", "0.00"], budget_ton_lines: ["6.40", "1.00"], target: "1 channel", status: "On Hold", displayed_date: "15 Sep 26 22:09", note: "Назначение строк не подтверждено" }],
    } } });
    render(<AdSnapshots />);
    expect(await screen.findByText(/79.35 TON/)).toHaveTextContent("$106.33");
    expect(screen.getByText(/Дата среза:/)).toHaveTextContent("не подтверждена");
    expect(screen.getByText(/Фильтр периода/)).toBeVisible();
    expect(screen.getByText("6.40")).toBeVisible();
    expect(screen.getByText(/Рублёвая окупаемость/)).toHaveTextContent("курс RUB не указан");
    expect(screen.getByText("Не являются оплатами")).toBeVisible();
  });
});

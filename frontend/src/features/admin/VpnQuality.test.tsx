import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VpnQuality } from "./VpnQuality";
import { useAdminRead } from "./useAdminRead";

vi.mock("./useAdminRead", () => ({ useAdminRead: vi.fn() }));
const mockRead = vi.mocked(useAdminRead);
beforeEach(() => vi.clearAllMocks());
function data(value: unknown) { mockRead.mockReturnValue({ data: value, loading: false, error: "", refresh: vi.fn() }); }

describe("серверные измерения VPN", () => {
  it("не выдаёт отсутствие конфигурации за падение VPN", () => {
    data({ locations: [], profiles: [], recent: [], stale_after_seconds: 900 });
    render(<VpnQuality />);
    expect(screen.getByText("Регулярные проверки не настроены.")).toBeInTheDocument();
    expect(screen.getByText(/не отражают все сети пользователей/)).toBeInTheDocument();
  });
  it("отделяет ошибку измерителя и неподтверждённый маршрут", () => {
    const now = new Date().toISOString();
    data({ locations: [{ location: "entry-de", enabled: true, started_at: now, active_until_utc: now, status: "measurement_error", reason: "collector_failed" }],
      profiles: [{ location: "entry-de", server_id: 1, route: 65001, label: "Автовыбор", attempts: 5, verified: 3, unverified: 1, measurement_errors: 1, errors: 0, slow: 1, p50_ms: 200, p95_ms: 1200, last_checked_at: now }],
      recent: [{ location: "entry-de", started_at: now, server_id: 1, route: 65001, label: "Автовыбор", result: "route_unknown", tls_ms: 150, ttfb_ms: 200 }], stale_after_seconds: 900 });
    render(<VpnQuality />);
    expect(screen.getByText(/Ошибка измерителя\./)).toBeInTheDocument();
    expect(screen.getByText("Ошибки измерителя: 1; маршрут не подтверждён: 1")).toBeInTheDocument();
    expect(screen.getByText("Маршрут не подтверждён")).toBeInTheDocument();
    expect(screen.getByText("200 мс / 1200 мс")).toBeInTheDocument();
  });
  it("показывает устаревшие наблюдения", () => {
    data({ locations: [{ location: "old", enabled: true, started_at: "2000-01-01T00:00:00Z", active_until_utc: null, status: "complete", reason: "" }], profiles: [], recent: [], stale_after_seconds: 900 });
    render(<VpnQuality />);
    expect(screen.getByText(/Нет свежих измерений/)).toBeInTheDocument();
  });
});

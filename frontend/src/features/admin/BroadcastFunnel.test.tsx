import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BroadcastFunnel } from "./BroadcastFunnel";
import { adminFetch } from "./api";
vi.mock("./api", () => ({ adminFetch: vi.fn() }));

describe("Воронка бонуса", () => {
  it("отделяет неизвестную историю от записанных оплат", async () => {
    vi.mocked(adminFetch).mockResolvedValue({ ok: true, data: { ok: true, jobs: [
      { job_id: "historical-id", created_at: "2026-09-01T10:00:00Z", first_recorded_at: null, claimed_users: 0, selected_users: 0, payment_users: 0, payments_created: 0, paid_users: 0, payments_succeeded: 0, revenue_kopecks: 0 },
      { job_id: "measured-id", created_at: "2026-09-25T10:00:00Z", first_recorded_at: "2026-09-25T11:00:00Z", claimed_users: 3, selected_users: 2, payment_users: 2, payments_created: 4, paid_users: 1, payments_succeeded: 1, revenue_kopecks: 9900 },
    ] } });
    render(<BroadcastFunnel />);
    expect(await screen.findByText("99.00 ₽")).toBeVisible();
    expect(screen.getByText("Нет записанных событий")).toBeVisible();
    expect(screen.getByText(/история не восстановлена/)).toBeVisible();
    expect(screen.getByText("4 / 1")).toBeVisible();
  });
});

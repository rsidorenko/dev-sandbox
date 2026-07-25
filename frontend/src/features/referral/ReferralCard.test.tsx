import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { referralApi } = vi.hoisted(() => ({
  referralApi: {
    getStats: vi.fn(),
  },
}));

vi.mock("@/entities/referral/api", () => ({ referralApi }));

import { ReferralCard } from "./ReferralCard";
import type { ReferralStatsResponse } from "@/entities/referral/types";

function statsPayload(overrides: Partial<ReferralStatsResponse["referral"]> = {}): ReferralStatsResponse {
  return {
    ok: true,
    referral: {
      code: "TESTREF1",
      web_link: "https://bravada-connect.ru/?ref=TESTREF1",
      telegram_link: "https://t.me/bravadavpn_bot?start=ref_TESTREF1",
      balance_rubles: 125.0,
      stats: {
        level1: { invited: 2, paid: 1 },
        level2: { invited: 1, paid: 0 },
        total_earned_rubles: 137.15,
      },
      recent_transactions: [
        {
          type: "referral_credit",
          amount_rubles: 87.15,
          plan_id: "1m",
          description: "L1 commission",
          created_at: "2026-07-12T10:00:00Z",
        },
        {
          type: "referral_credit",
          amount_rubles: 50.0,
          plan_id: "7d",
          description: "L1 commission",
          created_at: "2026-07-10T10:00:00Z",
        },
      ],
      ...overrides,
    },
  };
}

beforeEach(() => {
  referralApi.getStats.mockReset();
});

describe("ReferralCard", () => {
  it("renders balance, L1/L2 stats, links and recent transactions on success", async () => {
    referralApi.getStats.mockResolvedValue({ ok: true, data: statsPayload() });

    render(<ReferralCard />);

    // Balance + total earned
    expect(await screen.findByText(/125\.00/)).toBeInTheDocument();
    expect(screen.getByText(/137\.15/)).toBeInTheDocument();

    // L1 / L2 invited · paid
    expect(screen.getByText(/2 приглашено · 1 оплатило/)).toBeInTheDocument();
    expect(screen.getByText(/1 приглашено · 0 оплатило/)).toBeInTheDocument();

    // Conversion: L1 = 1/2 → 50%, L2 = 0/1 → 0%
    expect(screen.getAllByText(/конверсия:/)[0]).toHaveTextContent("50%");
    expect(screen.getAllByText(/конверсия:/)[1]).toHaveTextContent("0%");

    // Share links rendered (both web + telegram)
    expect(
      screen.getByText(/https:\/\/t\.me\/bravadavpn_bot\?start=ref_TESTREF1/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/https:\/\/bravada-connect\.ru\/\?ref=TESTREF1/),
    ).toBeInTheDocument();

    // Recent transactions: amounts + descriptions
    expect(screen.getAllByText(/\+87\.15/)).toHaveLength(1);
    expect(screen.getAllByText(/\+50\.00/)).toHaveLength(1);
    // Two "L1 commission" descriptions (one per tx)
    expect(screen.getAllByText("L1 commission")).toHaveLength(2);
  });

  it("shows the invite hint and hides the transactions list in empty state", async () => {
    referralApi.getStats.mockResolvedValue({
      ok: true,
      data: statsPayload({
        balance_rubles: 0,
        stats: {
          level1: { invited: 0, paid: 0 },
          level2: { invited: 0, paid: 0 },
          total_earned_rubles: 0,
        },
        recent_transactions: [],
      }),
    });

    render(<ReferralCard />);

    expect(
      await screen.findByText(/Отправьте ссылку другу/),
    ).toBeInTheDocument();
    // Zero invited on both levels → conversion guarded against div-by-zero ("—").
    expect(screen.getAllByText(/конверсия:/)[0]).toHaveTextContent("—");
    expect(screen.getAllByText(/конверсия:/)[1]).toHaveTextContent("—");
    // No transactions header when the list is empty.
    expect(screen.queryByText(/Последние начисления/)).not.toBeInTheDocument();
    // Links are still present even with no referrals yet.
    expect(
      screen.getByText(/https:\/\/bravada-connect\.ru\/\?ref=TESTREF1/),
    ).toBeInTheDocument();
  });

  it("renders an error message when the request fails", async () => {
    referralApi.getStats.mockResolvedValue({ ok: false, error: "internal_error" });

    render(<ReferralCard />);

    expect(
      await screen.findByText(/Не удалось загрузить данные/),
    ).toBeInTheDocument();
  });
});

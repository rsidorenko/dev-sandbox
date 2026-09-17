import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { referralApi } = vi.hoisted(() => ({
  referralApi: {
    getStats: vi.fn(),
    getTransactions: vi.fn(),
  },
}));

vi.mock("@/entities/referral/api", () => ({ referralApi }));

import { ReferralCard } from "./ReferralCard";
import type {
  ReferralStatsResponse,
  ReferralTransaction,
  ReferralTransactionsResponse,
} from "@/entities/referral/types";

function statsPayload(overrides: Partial<ReferralStatsResponse["referral"]> = {}): ReferralStatsResponse {
  return {
    ok: true,
    referral: {
      code: "TESTREF1",
      web_link: "https://bravada-connect.ru/?ref=TESTREF1",
      telegram_link: "https://t.me/bravadavpn_bot?start=ref_TESTREF1",
      balance_rubles: 125.0,
      program: {
        invitee_bonus_days: 3,
        qualifying_plan_min: "1m",
        max_level1_percent: 20,
        max_level2_percent: 2,
      },
      stats: {
        level1: { invited: 2, paid: 1 },
        level2: { invited: 1, paid: 0 },
        total_earned_rubles: 137.15,
        bonus_days_awarded: 30,
        invitee_bonus_days_received: 3,
      },
      // recent_transactions is still returned by the stats endpoint, but the card renders
      // the extended history from getTransactions() instead. Kept type-correct here.
      recent_transactions: [],
      ...overrides,
    },
  };
}

function transactionsPayload(txs: ReferralTransaction[] = []): ReferralTransactionsResponse {
  return { ok: true, transactions: txs, limit: 10, offset: 0, has_more: false };
}

const SAMPLE_TXS: ReferralTransaction[] = [
  {
    label: "Реферальное начисление",
    type: "referral_credit",
    amount_rubles: 87.15,
    level: 1,
    plan_id: "1m",
    created_at: "2026-07-12T10:00:00Z",
  },
  {
    label: "Реферальное начисление",
    type: "referral_credit",
    amount_rubles: 50.0,
    level: 2,
    plan_id: "7d",
    created_at: "2026-07-10T10:00:00Z",
  },
];

beforeEach(() => {
  referralApi.getStats.mockReset();
  referralApi.getTransactions.mockReset();
});

describe("ReferralCard", () => {
  it("renders balance, L1/L2 stats, links and the transaction history on success", async () => {
    referralApi.getStats.mockResolvedValue({ ok: true, data: statsPayload() });
    referralApi.getTransactions.mockResolvedValue({ ok: true, data: transactionsPayload(SAMPLE_TXS) });

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

    // Transaction history (clean labels, never raw descriptions): amounts + labels
    expect(screen.getAllByText(/\+87\.15/)).toHaveLength(1);
    expect(screen.getAllByText(/\+50\.00/)).toHaveLength(1);
    expect(screen.getAllByText("Реферальное начисление")).toHaveLength(2);
    // Security: the raw internal description must NEVER be rendered.
    expect(screen.queryByText(/webhook:/)).not.toBeInTheDocument();
  });

  it("shows the invite hint and an empty history placeholder when there are no transactions", async () => {
    referralApi.getStats.mockResolvedValue({
      ok: true,
      data: statsPayload({
        balance_rubles: 0,
        stats: {
          level1: { invited: 0, paid: 0 },
          level2: { invited: 0, paid: 0 },
          total_earned_rubles: 0,
          bonus_days_awarded: 0,
          invitee_bonus_days_received: 0,
        },
      }),
    });
    referralApi.getTransactions.mockResolvedValue({ ok: true, data: transactionsPayload([]) });

    render(<ReferralCard />);

    expect(await screen.findByText(/Отправьте ссылку другу/)).toBeInTheDocument();
    // Zero invited on both levels → conversion guarded against div-by-zero ("—").
    expect(screen.getAllByText(/конверсия:/)[0]).toHaveTextContent("—");
    expect(screen.getAllByText(/конверсия:/)[1]).toHaveTextContent("—");
    // History header is always present; empty list shows the placeholder.
    expect(screen.getByText(/История начислений/)).toBeInTheDocument();
    expect(screen.getByText(/Пока пусто\./)).toBeInTheDocument();
    // Links are still present even with no referrals yet.
    expect(
      screen.getByText(/https:\/\/bravada-connect\.ru\/\?ref=TESTREF1/),
    ).toBeInTheDocument();
  });

  it("renders an error message when the stats request fails", async () => {
    referralApi.getStats.mockResolvedValue({ ok: false, error: "internal_error" });
    referralApi.getTransactions.mockResolvedValue({ ok: true, data: transactionsPayload([]) });

    render(<ReferralCard />);

    expect(await screen.findByText(/Не удалось загрузить данные/)).toBeInTheDocument();
  });
});

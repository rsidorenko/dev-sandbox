import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QualitySection } from "./QualitySection";
import type {
  AdminDataHealth,
  AdminNotificationHoldout,
  AdminTrialCohort,
  AdminVpnQuality,
} from "@/entities/admin/types";

const DATA_HEALTH: AdminDataHealth = {
  sources: [
    { source: "funnel_events", detail: "trial_started", rows: 97, first_occurred_at: "2026-08-01T10:00:00Z" },
    { source: "connection_checks", detail: "check", rows: 12, first_occurred_at: "2026-09-19T08:00:00Z" },
  ],
  attribution_sources: [
    { campaign_key: "ads_apk_01", attributed_users: 40, excluded_from_analytics: false },
    { campaign_key: "nakrut", attributed_users: 350, excluded_from_analytics: true },
  ],
  campaigns_missing_spend: [{ campaign_key: "ads_apk_01", attributed_users: 40 }],
  chain_anomalies: {
    trials: 97,
    meaningful_without_route: 3,
    traffic_before_setup: 2,
    trials_without_setup: 10,
  },
};

const COHORTS: AdminTrialCohort[] = [
  {
    cohort_week: "2026-09-14",
    trials: 24,
    meaningful_users: 14,
    meaningful_rate: 0.5833,
    d1: { eligible_users: 20, retained_users: 8, rate: 0.4 },
    d2: { eligible_users: 13, retained_users: 4, rate: null },
    d3: { eligible_users: 0, retained_users: 0, rate: null },
    checkout_users: 2,
    mature_users: 0,
    paid_users: 0,
    paid_rate: null,
  },
];

const VPN_QUALITY: AdminVpnQuality = {
  servers: [
    {
      server_label: "🇳🇱 Нидерланды",
      checks: 8,
      connected_checks: 7,
      connected_rate: 0.875,
      users: 6,
      latency_p50_ms: 120,
      latency_p95_ms: 290,
      throughput_p50_kbps: 45000,
    },
  ],
  overall: {
    checks: 10,
    connected_checks: 8,
    connected_rate: 0.8,
    connected_users: 6,
    latency_p50_ms: 120,
    latency_p95_ms: 300,
  },
  web_verdicts: { confirmed_users: 4, problem_users: 2 },
  web_problem_reasons: [{ code: "connect_failed", users: 2 }],
};


const HOLDOUT: AdminNotificationHoldout = {
  trial_expiring_24h: {
    delivered: { users: 40, total_users: 45, pending_users: 5, paid_users: 6, paid_rate: 0.15 },
    holdout: { users: 5, total_users: 6, pending_users: 1, paid_users: 0, paid_rate: 0.0 },
  },
};

describe("QualitySection", () => {
  it("renders data health: sources, missing spend warning, nakrut marker, anomalies", () => {
    render(<QualitySection dataHealth={DATA_HEALTH} trialCohorts={[]} vpnQuality={undefined} />);
    expect(screen.getByText("Качество данных")).toBeInTheDocument();
    expect(screen.getByText(/funnel_events\/trial_started/)).toBeInTheDocument();
    expect(screen.getByText(/2026-08-01 · 97/)).toBeInTheDocument();
    expect(screen.getByText(/Расходы не внесены/)).toBeInTheDocument();
    expect(screen.getByText(/ads_apk_01: 40 польз\./)).toBeInTheDocument();
    expect(screen.getByText(/nakrut \(исключён\): 350/)).toBeInTheDocument();
    expect(screen.getByText(/без шага подключения: 10/)).toBeInTheDocument();
    expect(screen.getByText(/значимый трафик без маршрута: 3/)).toBeInTheDocument();
  });

  it("renders cohort table with retention and paid columns", () => {
    render(<QualitySection dataHealth={undefined} trialCohorts={COHORTS} vpnQuality={undefined} />);
    expect(screen.getByText("Когорты триалов по неделям")).toBeInTheDocument();
    expect(screen.getByText(/с 14 сентября 2026/)).toBeInTheDocument();
    expect(screen.getByText("14 (58.3%)")).toBeInTheDocument();
    expect(screen.getByText("8/20 (40%)")).toBeInTheDocument();
    expect(screen.getByText("4/13")).toBeInTheDocument();
  });

  it("renders vpn quality: overall, servers, web verdicts and reasons", () => {
    render(<QualitySection dataHealth={undefined} trialCohorts={[]} vpnQuality={VPN_QUALITY} />);
    expect(screen.getByText("Качество VPN")).toBeInTheDocument();
    expect(screen.getByText(/Проверок: 10 · подключено 80%/)).toBeInTheDocument();
    expect(screen.getByText("🇳🇱 Нидерланды")).toBeInTheDocument();
    expect(screen.getByText("7 (87.5%)")).toBeInTheDocument();
    expect(screen.getByText("45 Мбит/с")).toBeInTheDocument();
    expect(screen.getByText(/«всё работает» — 4/)).toBeInTheDocument();
    expect(screen.getByText(/VPN не подключается: 2/)).toBeInTheDocument();
  });

  it("renders empty state for vpn quality without checks", () => {
    render(
      <QualitySection
        dataHealth={undefined}
        trialCohorts={[]}
        vpnQuality={{ ...VPN_QUALITY, overall: { ...VPN_QUALITY.overall, checks: 0, connected_rate: null } }}
      />,
    );
    expect(screen.getByText("Проверок /check за период не было")).toBeInTheDocument();
  });

  it("renders notification holdout comparison table", () => {
    render(
      <QualitySection dataHealth={undefined} trialCohorts={[]} vpnQuality={undefined} notificationHoldout={HOLDOUT} />,
    );
    expect(screen.getByText("Эффект уведомлений (holdout)")).toBeInTheDocument();
    expect(screen.getByText("trial_expiring_24h")).toBeInTheDocument();
    expect(screen.getByText("40/45 (5 ожидают)")).toBeInTheDocument();
    expect(screen.getByText("5/6 (1 ожидают)")).toBeInTheDocument();
    expect(screen.getByText("6 (15%)")).toBeInTheDocument();
    expect(screen.getByText("+15 п.п.")).toBeInTheDocument();
  });

  it("renders section shell when no optional data provided", () => {
    render(<QualitySection dataHealth={undefined} trialCohorts={[]} vpnQuality={undefined} />);
    expect(screen.getByText("Качество и данные")).toBeInTheDocument();
    expect(screen.queryByText("Качество данных")).not.toBeInTheDocument();
    expect(screen.queryByText("Когорты триалов по неделям")).not.toBeInTheDocument();
  });
});

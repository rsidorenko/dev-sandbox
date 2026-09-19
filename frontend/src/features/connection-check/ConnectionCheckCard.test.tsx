import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { checkApi, medianLatency, detectPlatformFromUA } = vi.hoisted(() => ({
  checkApi: {
    status: vi.fn(),
    ping: vi.fn(),
    speed: vi.fn(),
    result: vi.fn(),
    feedback: vi.fn(),
  },
  medianLatency: (samples: Array<number | null>) => {
    const values = samples.filter((v): v is number => v !== null);
    if (values.length === 0) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  },
  detectPlatformFromUA: () => "android",
}));

vi.mock("@/entities/connection-check/api", () => ({
  checkApi,
  medianLatency,
  detectPlatformFromUA,
}));

import { ConnectionCheckCard } from "./ConnectionCheckCard";

function mockHappyPath(overrides: Partial<{ connected: boolean | null; label: string }> = {}) {
  checkApi.status.mockResolvedValue({
    ok: true,
    data: {
      enabled: true,
      connected: overrides.connected !== undefined ? overrides.connected : true,
      server_label: overrides.label ?? "🇳🇱 Нидерланды",
      server_country: "NL",
    },
  });
  checkApi.ping.mockResolvedValue(90);
  checkApi.speed.mockResolvedValue(45000);
  checkApi.result.mockResolvedValue({ ok: true, data: { stored: true } });
  checkApi.feedback.mockResolvedValue({ ok: true, data: { feedback_stored: true } });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ConnectionCheckCard", () => {
  it("shows connected verdict with server, latency and speed, then records result", async () => {
    mockHappyPath();
    render(<ConnectionCheckCard token="tok123" />);

    expect(await screen.findByText("VPN подключён")).toBeInTheDocument();
    expect(screen.getByText(/Нидерланды/)).toBeInTheDocument();
    expect(screen.getByText("≈ 90 мс")).toBeInTheDocument();
    expect(screen.getByText("45.0 Мбит/с")).toBeInTheDocument();

    await waitFor(() =>
      expect(checkApi.result).toHaveBeenCalledWith("tok123", {
        latency_ms: 90,
        throughput_kbps: 45000,
        platform: "android",
      }),
    );
    expect(checkApi.ping).toHaveBeenCalledTimes(3);
  });

  it("shows warning and hint when VPN is not detected", async () => {
    mockHappyPath({ connected: false });
    render(<ConnectionCheckCard token="tok123" />);

    expect(await screen.findByText("VPN не обнаружен")).toBeInTheDocument();
    expect(screen.getByText("🔄 Проверить снова")).toBeInTheDocument();
  });

  it("shows unknown state when servers cannot be resolved", async () => {
    mockHappyPath({ connected: null, label: "" });
    render(<ConnectionCheckCard token="tok123" />);

    expect(await screen.findByText("Не удалось проверить автоматически")).toBeInTheDocument();
  });

  it("collects a reason after 'Есть проблема' and sends feedback", async () => {
    mockHappyPath();
    render(<ConnectionCheckCard token="tok123" />);
    await screen.findByText("VPN подключён");

    await userEvent.click(screen.getByText("⚠️ Есть проблема"));
    await userEvent.click(screen.getByText("🔌 VPN не подключается"));

    await waitFor(() =>
      expect(checkApi.feedback).toHaveBeenCalledWith("tok123", {
        verdict: "problem",
        reason_code: "connect_failed",
        platform: "android",
      }),
    );
    expect(await screen.findByText("Спасибо!")).toBeInTheDocument();
  });

  it("sends ok verdict without reason on 'Всё работает'", async () => {
    mockHappyPath();
    render(<ConnectionCheckCard token="tok123" />);
    await screen.findByText("VPN подключён");

    await userEvent.click(screen.getByText("✅ Всё работает"));

    await waitFor(() =>
      expect(checkApi.feedback).toHaveBeenCalledWith("tok123", {
        verdict: "ok",
        platform: "android",
      }),
    );
    expect(await screen.findByText("Спасибо!")).toBeInTheDocument();
  });

  it("does not show thanks when feedback API fails", async () => {
    mockHappyPath();
    checkApi.feedback.mockResolvedValueOnce({ ok: false, error: "network_error" });
    render(<ConnectionCheckCard token="tok123" />);
    await screen.findByText("VPN подключён");

    await userEvent.click(screen.getByText("✅ Всё работает"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось отправить ответ");
    expect(screen.queryByText("Спасибо!")).not.toBeInTheDocument();
    expect(screen.getByText("✅ Всё работает")).toBeInTheDocument();
  });

  it("renders invalid-link state on 404", async () => {
    checkApi.status.mockResolvedValue({ ok: false, error: "not_found" });
    render(<ConnectionCheckCard token="bad" />);
    expect(await screen.findByText("Ссылка недействительна")).toBeInTheDocument();
  });

  it("renders expired state on 403", async () => {
    checkApi.status.mockResolvedValue({ ok: false, error: "subscription_expired" });
    render(<ConnectionCheckCard token="tok" />);
    expect(await screen.findByText("Подписка истекла")).toBeInTheDocument();
  });

  it("renders disabled state when feature flag is off", async () => {
    checkApi.status.mockResolvedValue({
      ok: true,
      data: { enabled: false, connected: null },
    });
    render(<ConnectionCheckCard token="tok" />);
    expect(
      await screen.findByText("Проверка подключения временно отключена"),
    ).toBeInTheDocument();
    expect(checkApi.ping).not.toHaveBeenCalled();
  });
});

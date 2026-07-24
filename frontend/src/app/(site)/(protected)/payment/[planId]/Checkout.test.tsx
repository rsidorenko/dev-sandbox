import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { auth, paymentApi, params } = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false, profile: null as null | { referral: { balance_rubles: number; code: string; referrals_count: number } } },
  paymentApi: { create: vi.fn(), payWithBalance: vi.fn() },
  params: { planId: "1m" },
}));

vi.mock("@/shared/lib/auth", () => ({
  useAuth: () => ({
    loading: auth.loading,
    authenticated: auth.authenticated,
    profile: auth.profile,
    refresh: vi.fn(),
  }),
}));
vi.mock("@/entities/payment/api", () => ({ paymentApi }));
vi.mock("next/navigation", () => ({
  useParams: () => params,
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/payment/1m",
}));

import PaymentPage from "./page";

const originalLocation = window.location;

beforeEach(() => {
  auth.authenticated = true;
  auth.loading = false;
  auth.profile = null;
  params.planId = "1m";
  paymentApi.create.mockReset();
  paymentApi.payWithBalance.mockReset();
  Object.defineProperty(window, "location", {
    value: { ...originalLocation, href: "" },
    writable: true,
    configurable: true,
  });
});

describe("Checkout (payment/[planId])", () => {
  it("renders 'Тариф не найден' for an unknown plan", () => {
    params.planId = "nope";
    render(<PaymentPage />);
    expect(screen.getByText("Тариф не найден")).toBeInTheDocument();
  });

  it("shows the AuthGate (email + Telegram) for an unauthenticated visitor", () => {
    auth.authenticated = false;
    auth.loading = false;
    render(<PaymentPage />);
    expect(
      screen.getByRole("link", { name: "Войти по email" }),
    ).toHaveAttribute("href", "/login?next=/payment/1m");
    const botLink = screen.getByRole("link", { name: /Telegram-бот/ });
    expect(botLink.getAttribute("href")).toMatch(
      /^https:\/\/t\.me\/.+bot\?start=1m$/,
    );
  });

  it("renders the checkout form with the base total for an authenticated visitor", () => {
    render(<PaymentPage />);
    expect(screen.getByText("Оформление подписки")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Оплатить картой 249 ₽/ })).toBeInTheDocument();
  });

  it("updates the total when the device slider increases", () => {
    render(<PaymentPage />);
    const slider = document.querySelector(
      'input[type="range"]',
    ) as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "10" } });
    // 5 доп. устройств × round(80/30*30)=80 → +400 → 249+400 = 649.
    expect(
      screen.getByRole("button", { name: /Оплатить картой 649 ₽/ }),
    ).toBeInTheDocument();
  });

  it("shows the balance-pay button when the user has enough balance", () => {
    auth.profile = {
      referral: { balance_rubles: 1000, code: "X", referrals_count: 0 },
    };
    render(<PaymentPage />);
    expect(
      screen.getByRole("button", { name: /Оплатить с баланса/ }),
    ).toBeInTheDocument();
  });

  it("hides the balance-pay button when balance is insufficient", () => {
    auth.profile = {
      referral: { balance_rubles: 100, code: "X", referrals_count: 0 },
    };
    render(<PaymentPage />);
    expect(
      screen.queryByRole("button", { name: /Оплатить с баланса/ }),
    ).not.toBeInTheDocument();
  });

  it("pays with balance and refreshes on success", async () => {
    auth.profile = {
      referral: { balance_rubles: 1000, code: "X", referrals_count: 0 },
    };
    paymentApi.payWithBalance.mockResolvedValue({
      ok: true,
      data: { status: "completed", active_until: "2099-01-01", balance_rubles_remaining: 751 },
    });
    const user = userEvent.setup();
    render(<PaymentPage />);
    await user.click(screen.getByRole("button", { name: /Оплатить с баланса/ }));
    await waitFor(() => {
      expect(paymentApi.payWithBalance).toHaveBeenCalled();
    });
    // Card-pay button must NOT have been used.
    expect(paymentApi.create).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/Подписка оплачена с реферального баланса/),
    ).toBeInTheDocument();
  });

  it("redirects to payment_url on a successful create", async () => {
    paymentApi.create.mockResolvedValue({
      ok: true,
      data: { payment_url: "https://pay.example/x" },
    });
    const user = userEvent.setup();
    render(<PaymentPage />);
    await user.click(screen.getByRole("button", { name: /Оплатить/ }));
    await waitFor(() => {
      expect(window.location.href).toBe("https://pay.example/x");
    });
    expect(paymentApi.create).toHaveBeenCalledWith("1m", 5);
  });

  it("shows an Alert when payment is unavailable", async () => {
    paymentApi.create.mockResolvedValue({
      ok: true,
      data: {
        status: "payment_unavailable",
        amount_rubles: 249,
        message: "Оплата временно недоступна",
      },
    });
    const user = userEvent.setup();
    render(<PaymentPage />);
    await user.click(screen.getByRole("button", { name: /Оплатить/ }));
    expect(
      await screen.findByText("Оплата временно недоступна"),
    ).toBeInTheDocument();
  });
});

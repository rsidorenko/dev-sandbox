import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { auth, paymentApi, params } = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false },
  paymentApi: { create: vi.fn(), validatePromo: vi.fn() },
  params: { planId: "1m" },
}));

vi.mock("@/shared/lib/auth", () => ({
  useAuth: () => ({
    loading: auth.loading,
    authenticated: auth.authenticated,
    profile: null,
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
  params.planId = "1m";
  paymentApi.create.mockReset();
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
    expect(screen.getByRole("button", { name: /Оплатить 249 ₽/ })).toBeInTheDocument();
  });

  it("updates the total when the device slider increases", () => {
    render(<PaymentPage />);
    const slider = document.querySelector(
      'input[type="range"]',
    ) as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "10" } });
    // 5 доп. устройств × round(80/30*30)=80 → +400 → 249+400 = 649.
    expect(
      screen.getByRole("button", { name: /Оплатить 649 ₽/ }),
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
    // No promo applied in this test → create is called with promoCode=undefined.
    expect(paymentApi.create).toHaveBeenCalledWith("1m", 5, undefined);
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

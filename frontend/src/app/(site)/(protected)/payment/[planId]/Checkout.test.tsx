vi.mock("@/features/plans/PlansProvider", () => ({
  usePlans: () => ({catalog:{ok:true,plans:[
    {plan_id:"1d",duration_days:1,base_rubles:12}, {plan_id:"7d",duration_days:7,base_rubles:99},
    {plan_id:"14d",duration_days:14,base_rubles:169}, {plan_id:"1m",duration_days:30,base_rubles:249},
    {plan_id:"3m",duration_days:90,base_rubles:699}, {plan_id:"6m",duration_days:180,base_rubles:1259},
    {plan_id:"365d",duration_days:365,base_rubles:2199}],global_discount:null,custom_day_price_rubles:15,extra_device_price_rubles:80,devices:{base:5,max:20}},
    prices:{"1d":12,"7d":99,"14d":169,"1m":249,"3m":699,"6m":1259,"365d":2199},globalDiscount:null,loaded:true,reload:vi.fn()}),
  CatalogUnavailable: () => <p>Нет каталога</p>,
}));
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { auth, paymentApi, experimentApi, params, navigation, authApi } = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false, profile: null as null | { referral: { balance_rubles: number; code: string; referrals_count: number } } },
  paymentApi: { create: vi.fn(), payWithBalance: vi.fn() },
  experimentApi: { resolve: vi.fn() },
  authApi: { requestLink: vi.fn() },
  params: { planId: "1m" },
  navigation: { query: "" },
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
vi.mock("@/entities/experiment/api", () => ({ experimentApi }));
vi.mock("@/features/auth/api", () => ({ authApi }));
vi.mock("next/navigation", () => ({
  useParams: () => params,
  useSearchParams: () => new URLSearchParams(navigation.query),
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
  navigation.query = "";
  paymentApi.create.mockReset();
  paymentApi.payWithBalance.mockReset();
  authApi.requestLink.mockReset();
  experimentApi.resolve.mockReset();
  experimentApi.resolve.mockResolvedValue({
    ok: true,
    data: {
      ok: true,
      experiment: { key: "checkout_reassurance_v1", variant: "control", assigned_at: "2026-09-17T00:00:00Z" },
    },
  });
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

  it("предлагает вставить или создать ссылку без email", () => {
    auth.authenticated = false;
    render(<PaymentPage />);
    expect(screen.getByLabelText("Ссылка подписки")).toBeInTheDocument();
    expect(screen.getByRole("button", {name:"Получить ссылку подписки"})).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("email@example.com")).not.toBeInTheDocument();
  });

  it("renders the checkout form with the base total for an authenticated visitor", () => {
    render(<PaymentPage />);
    expect(screen.getByText("Оформление подписки")).toBeInTheDocument();
    expect(screen.getByText("5 устройств включено")).toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Перейти к оплате 249 ₽/ })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Промокод")).not.toBeInTheDocument();
  });

  it("shows reassurance copy only for the reassurance experiment variant", async () => {
    experimentApi.resolve.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        experiment: {
          key: "checkout_reassurance_v1",
          variant: "reassurance",
          assigned_at: "2026-09-17T00:00:00Z",
        },
      },
    });
    render(<PaymentPage />);
    expect(await screen.findByText(/Безопасная оплата/)).toBeInTheDocument();
  });

  it("keeps the control checkout when experiment resolution fails", async () => {
    experimentApi.resolve.mockResolvedValue({ ok: false, error: "network_error" });
    render(<PaymentPage />);
    await waitFor(() => expect(experimentApi.resolve).toHaveBeenCalled());
    expect(screen.queryByText(/Безопасная оплата/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Перейти к оплате 249 ₽/ })).toBeInTheDocument();
  });

  it("reveals extra devices on demand and updates the total", async () => {
    const user = userEvent.setup();
    render(<PaymentPage />);
    await user.click(screen.getByRole("button", { name: "Нужно больше?" }));
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, { target: { value: "10" } });
    // 5 доп. устройств × round(80/30*30)=80 → +400 → 249+400 = 649.
    expect(
      screen.getByRole("button", { name: /Перейти к оплате 649 ₽/ }),
    ).toBeInTheDocument();
  });

  it("restores the device picker from the devices query parameter", () => {
    navigation.query = "devices=9";
    render(<PaymentPage />);
    expect(screen.getByRole("slider")).toHaveValue("9");
    expect(screen.getByText("9 устройств включено")).toBeInTheDocument();
  });

  it("reveals promo and alternative payment options only on demand", async () => {
    const user = userEvent.setup();
    render(<PaymentPage />);
    await user.click(screen.getByRole("button", { name: "Есть промокод?" }));
    expect(screen.getByPlaceholderText("Промокод")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Другой способ" }));
    expect(screen.getByRole("button", { name: /Криптовалюта/ })).toBeInTheDocument();
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
    await user.click(screen.getByRole("checkbox"));
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

  it("показывает принятую оплату и блокирует повторную покупку при настройке доступа", async () => {
    auth.profile = { referral: { balance_rubles: 1000, code: "X", referrals_count: 0 } };
    paymentApi.payWithBalance.mockResolvedValue({
      ok: true, data: { status: "provisioning", balance_rubles_remaining: 751 },
    });
    const user = userEvent.setup();
    render(<PaymentPage />);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /Оплатить с баланса/ }));
    expect(await screen.findByText(/Повторно платить не нужно/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Оплатить с баланса/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Перейти к оплате/ })).toBeDisabled();
    expect(paymentApi.payWithBalance).toHaveBeenCalledTimes(1);
    expect(paymentApi.create).not.toHaveBeenCalled();
  });

  it("redirects to payment_url on a successful create", async () => {
    paymentApi.create.mockResolvedValue({
      ok: true,
      data: { payment_url: "https://pay.example/x" },
    });
    const user = userEvent.setup();
    render(<PaymentPage />);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /Перейти к оплате/ }));
    await waitFor(() => {
      expect(window.location.href).toBe("https://pay.example/x");
    });
    expect(paymentApi.create).toHaveBeenCalledWith("1m", 5, "card", 24900, true);
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
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /Перейти к оплате/ }));
    expect(
      await screen.findByText("Оплата временно недоступна"),
    ).toBeInTheDocument();
  });
});

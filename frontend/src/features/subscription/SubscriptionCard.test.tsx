import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

const { addDevicesWithBalance } = vi.hoisted(() => ({ addDevicesWithBalance: vi.fn() }));
vi.mock("@/entities/payment/api", () => ({ paymentApi: { addDevicesWithBalance } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { SubscriptionCard } from "./SubscriptionCard";

it("сообщает о принятой оплате устройств при ожидающей выдаче", async () => {
  addDevicesWithBalance.mockResolvedValue({ ok: true, data: { status: "provisioning" } });
  const user = userEvent.setup();
  const onRefresh = vi.fn().mockResolvedValue(undefined);
  render(<SubscriptionCard
    sub={{ state: "active", active_until: "2099-01-01", remaining_days: 30, plan_id: "1m", device_count: 5 }}
    referral={{ code: "REF", balance_rubles: 1000, referrals_count: 0 }}
    onRefresh={onRefresh} onOpenKeys={vi.fn()} onRenew={vi.fn()}
  />);
  await user.click(screen.getByRole("button", { name: /Устройства/ }));
  fireEvent.change(screen.getByRole("slider"), { target: { value: "6" } });
  await user.click(screen.getByRole("button", { name: /С баланса/ }));
  expect(await screen.findByRole("status")).toHaveTextContent("Повторно платить не нужно");
  expect(screen.queryByRole("button", { name: /С баланса/ })).not.toBeInTheDocument();
  expect(addDevicesWithBalance).toHaveBeenCalledTimes(1);
  expect(onRefresh).toHaveBeenCalledTimes(1);
});

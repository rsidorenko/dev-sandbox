vi.mock("@/features/plans/PlansProvider", () => ({
  usePlans: () => ({catalog:{ok:true,plans:[
    {plan_id:"1d",duration_days:1,base_rubles:12}, {plan_id:"7d",duration_days:7,base_rubles:99},
    {plan_id:"14d",duration_days:14,base_rubles:169}, {plan_id:"1m",duration_days:30,base_rubles:249},
    {plan_id:"3m",duration_days:90,base_rubles:699}, {plan_id:"6m",duration_days:180,base_rubles:1259},
    {plan_id:"365d",duration_days:365,base_rubles:2199}],global_discount:null,custom_day_price_rubles:15,extra_device_price_rubles:80,devices:{base:5,max:20}},
    prices:{"1d":12,"7d":99,"14d":169,"1m":249,"3m":699,"6m":1259,"365d":2199},globalDiscount:null,loaded:true,reload:vi.fn()}),
  CatalogUnavailable: () => <p>Нет каталога</p>,
}));
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
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: /С баланса/ }));
  expect(await screen.findByRole("status")).toHaveTextContent("Повторно платить не нужно");
  expect(screen.queryByRole("button", { name: /С баланса/ })).not.toBeInTheDocument();
  expect(addDevicesWithBalance).toHaveBeenCalledTimes(1);
  expect(onRefresh).toHaveBeenCalledTimes(1);
});

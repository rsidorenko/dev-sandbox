vi.mock("@/features/plans/PlansProvider", () => ({
  usePlans: () => ({catalog:{ok:true,plans:[
    {plan_id:"1d",duration_days:1,base_rubles:12}, {plan_id:"7d",duration_days:7,base_rubles:99},
    {plan_id:"14d",duration_days:14,base_rubles:169}, {plan_id:"1m",duration_days:30,base_rubles:249},
    {plan_id:"3m",duration_days:90,base_rubles:699}, {plan_id:"6m",duration_days:180,base_rubles:1259},
    {plan_id:"365d",duration_days:365,base_rubles:2199}],global_discount:null,custom_day_price_rubles:15,extra_device_price_rubles:80,devices:{base:5,max:20}},
    prices:{"1d":12,"7d":99,"14d":169,"1m":249,"3m":699,"6m":1259,"365d":2199},globalDiscount:null,loaded:true,reload:vi.fn()}),
  CatalogUnavailable: () => <p>Нет каталога</p>,
}));
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TariffsSection } from "./TariffsSection";

describe("TariffsSection", () => {
  it("shows three primary tariffs and recommends three months", () => {
    render(<TariffsSection />);
    expect(screen.getByRole("button", { name: /1 месяц/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /3 месяца/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /1 год/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /1 день/ })).not.toBeInTheDocument();
    expect(screen.getByText("Рекомендуем")).toBeInTheDocument();
    expect(screen.getByText("699 ₽")).toBeInTheDocument();
  });

  it("reveals short, six-month and custom variants on demand", async () => {
    const user = userEvent.setup();
    render(<TariffsSection />);
    await user.click(screen.getByRole("button", { name: /Другие сроки/ }));
    expect(screen.getByRole("button", { name: /1 день/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /6 месяцев/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свой срок" })).toBeInTheDocument();
  });

  it("selecting '1 год' shows the price and the 'Лучшая цена' badge", async () => {
    const user = userEvent.setup();
    render(<TariffsSection />);
    await user.click(screen.getByRole("button", { name: /1 год/ }));
    expect(screen.getByText("2 199 ₽")).toBeInTheDocument();
    expect(screen.getAllByText("Лучшая цена").length).toBeGreaterThan(0);
  });

  it("the CTA links to /payment/<id> for the selected tariff", async () => {
    const user = userEvent.setup();
    render(<TariffsSection />);
    await user.click(screen.getByRole("button", { name: /1 год/ }));
    expect(screen.getByRole("link", { name: "Оформить подписку" })).toHaveAttribute(
      "href",
      "/payment/365d",
    );
  });

  it("shows the savings badge for '1 месяц' (−31%)", async () => {
    const user = userEvent.setup();
    render(<TariffsSection />);
    await user.click(screen.getByRole("button", { name: /1 месяц/ }));
    expect(screen.getByText(/Выгода −31%/)).toBeInTheDocument();
  });

  it("the custom-tariff toggle shows 'Свой тариф' and links to /payment/custom:30", async () => {
    const user = userEvent.setup();
    render(<TariffsSection />);
    await user.click(screen.getByRole("button", { name: /Другие сроки/ }));
    await user.click(screen.getByRole("button", { name: "Свой срок" }));
    expect(
      screen.getByRole("heading", { name: "Свой тариф" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Оформить подписку" })).toHaveAttribute(
      "href",
      "/payment/custom:30",
    );
  });
});

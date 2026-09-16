import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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

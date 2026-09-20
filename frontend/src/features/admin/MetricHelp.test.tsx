import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetricHelp } from "./MetricHelp";

describe("MetricHelp", () => {
  it("renders an accessible disclosure with explanatory text", () => {
    render(<MetricHelp label="CAC">Расход на одного нового платящего клиента.</MetricHelp>);

    const trigger = screen.getByLabelText("Справка: CAC");
    const details = trigger.closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Расход на одного нового платящего клиента");

    fireEvent.click(trigger);
    expect(details).toHaveAttribute("open");
  });
});

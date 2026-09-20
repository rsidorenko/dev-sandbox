import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { METRIC_HELP_AUTO_CLOSE_MS, MetricHelp } from "./MetricHelp";

describe("MetricHelp", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("opens on click and closes automatically", () => {
    vi.useFakeTimers();
    render(<MetricHelp label="CAC">Расход на одного нового платящего клиента.</MetricHelp>);

    const trigger = screen.getByLabelText("Справка: CAC");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Расход на одного нового платящего клиента");

    act(() => vi.advanceTimersByTime(METRIC_HELP_AUTO_CLOSE_MS));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("closes immediately on second click, Escape or outside press", () => {
    vi.useFakeTimers();
    render(<MetricHelp label="CAC">Описание.</MetricHelp>);
    const trigger = screen.getByLabelText("Справка: CAC");

    fireEvent.click(trigger);
    fireEvent.click(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("keeps desktop hover behavior without pinning the tooltip", () => {
    render(<MetricHelp label="CAC">Описание.</MetricHelp>);
    const trigger = screen.getByLabelText("Справка: CAC");

    fireEvent.mouseEnter(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.mouseLeave(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});

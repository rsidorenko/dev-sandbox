import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { STATUS_DESCRIPTORS, StatusLegend } from "./StatusLegend";

describe("StatusLegend", () => {
  it("объясняет все ключевые статусы", () => {
    render(<StatusLegend />);
    for (const s of STATUS_DESCRIPTORS) {
      expect(screen.getByText(s.title)).toBeInTheDocument();
    }
  });

  it("явно объясняет «прочерк» (нет подписки) и что продлить нельзя", () => {
    render(<StatusLegend />);
    expect(screen.getByText(/Нет подписки/)).toBeInTheDocument();
    expect(screen.getByText(/Продлить нельзя/)).toBeInTheDocument();
  });

  it("упоминает 20-дневный льготный период для expired", () => {
    render(<StatusLegend />);
    expect(screen.getByText(/20 дней льготы/)).toBeInTheDocument();
  });
});

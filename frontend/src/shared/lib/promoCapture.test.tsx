import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const promo = vi.hoisted(() => ({ value: "BRAVADA-XYZ" }));

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(promo.value ? `promo=${promo.value}` : ""),
  useRouter: () => ({}),
  usePathname: () => "/",
  useParams: () => ({}),
}));

import { PromoCapture } from "./promoCapture";

beforeEach(() => {
  localStorage.clear();
});

describe("PromoCapture", () => {
  it("stores the promo param in localStorage as pending_promo_code", async () => {
    promo.value = "BRAVADA-XYZ";
    render(<PromoCapture />);
    await waitFor(() => {
      expect(localStorage.getItem("pending_promo_code")).toBe("BRAVADA-XYZ");
    });
  });

  it("does not write anything when there is no promo param", async () => {
    promo.value = "";
    render(<PromoCapture />);
    await waitFor(() => {
      expect(localStorage.getItem("pending_promo_code")).toBeNull();
    });
  });
});

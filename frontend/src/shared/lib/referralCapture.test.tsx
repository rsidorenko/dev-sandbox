import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const ref = vi.hoisted(() => ({ value: "ABC123" }));

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(ref.value ? `ref=${ref.value}` : ""),
  useRouter: () => ({}),
  usePathname: () => "/",
  useParams: () => ({}),
}));

import { ReferralCapture } from "./referralCapture";

beforeEach(() => {
  localStorage.clear();
});

describe("ReferralCapture", () => {
  it("stores the ref param in localStorage as pending_referral_code", async () => {
    ref.value = "ABC123";
    render(<ReferralCapture />);
    await waitFor(() => {
      expect(localStorage.getItem("pending_referral_code")).toBe("ABC123");
    });
  });

  it("does not write anything when there is no ref param", async () => {
    ref.value = "";
    render(<ReferralCapture />);
    await waitFor(() => {
      expect(localStorage.getItem("pending_referral_code")).toBeNull();
    });
  });
});

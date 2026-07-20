import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ref = vi.hoisted(() => ({ value: "ABC123" }));

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(ref.value ? `ref=${ref.value}` : ""),
  useRouter: () => ({}),
  usePathname: () => "/",
  useParams: () => ({}),
}));

import { REFERRAL_TTL_MS, ReferralCapture, readPendingReferralCode } from "./referralCapture";

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

  it("stamps a capture timestamp alongside the code", async () => {
    ref.value = "TS1";
    render(<ReferralCapture />);
    await waitFor(() => {
      expect(localStorage.getItem("pending_referral_code")).toBe("TS1");
    });
    expect(localStorage.getItem("pending_referral_code_ts")).not.toBeNull();
  });
});

describe("readPendingReferralCode (TTL)", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 0, 1, 0, 0, 0));
  });
  afterEach(() => vi.useRealTimers());

  it("returns a freshly captured code", () => {
    localStorage.setItem("pending_referral_code", "FRESH1");
    localStorage.setItem("pending_referral_code_ts", String(Date.now()));
    expect(readPendingReferralCode()).toBe("FRESH1");
  });

  it("returns null and cleans up a stale code (older than TTL)", () => {
    localStorage.setItem("pending_referral_code", "OLD1");
    localStorage.setItem("pending_referral_code_ts", String(Date.now() - REFERRAL_TTL_MS - 1));
    expect(readPendingReferralCode()).toBeNull();
    expect(localStorage.getItem("pending_referral_code")).toBeNull();
    expect(localStorage.getItem("pending_referral_code_ts")).toBeNull();
  });

  it("returns null when nothing was captured", () => {
    expect(readPendingReferralCode()).toBeNull();
  });
});

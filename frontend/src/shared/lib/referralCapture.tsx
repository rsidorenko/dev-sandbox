"use client";

import { useEffect } from "react";
import { useSearchParams } from "next/navigation";

const REFERRAL_CODE_KEY = "pending_referral_code";
const REFERRAL_TS_KEY = "pending_referral_code_ts";
/** Stale-attribution window: a ref captured longer ago than this is ignored. */
export const REFERRAL_TTL_MS = 24 * 60 * 60 * 1000; // 24 часа

/**
 * Read a non-stale pending referral code from localStorage, or null if absent/expired.
 * Used by the login flow to attach the inviter on first web registration. Cleans up
 * an expired entry so it doesn't resurface later.
 */
export function readPendingReferralCode(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const code = window.localStorage.getItem(REFERRAL_CODE_KEY);
    if (!code) return null;
    const tsStr = window.localStorage.getItem(REFERRAL_TS_KEY);
    if (tsStr) {
      const ts = Number(tsStr);
      if (Number.isFinite(ts) && Date.now() - ts > REFERRAL_TTL_MS) {
        window.localStorage.removeItem(REFERRAL_CODE_KEY);
        window.localStorage.removeItem(REFERRAL_TS_KEY);
        return null;
      }
    }
    return code;
  } catch {
    return null;
  }
}

/** Clear the captured referral code (after successful verification or logout). */
export function clearPendingReferralCode(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(REFERRAL_CODE_KEY);
    window.localStorage.removeItem(REFERRAL_TS_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Invisible client component that captures the `ref` query parameter from the URL
 * and stores it in localStorage (with a capture timestamp) for later use during
 * email verification (web referral tracking). Mounted in the root layout so it
 * runs on every page.
 */
export function ReferralCapture() {
  const searchParams = useSearchParams();

  useEffect(() => {
    const ref = searchParams.get("ref");
    if (ref) {
      try {
        localStorage.setItem(REFERRAL_CODE_KEY, ref);
        localStorage.setItem(REFERRAL_TS_KEY, String(Date.now()));
      } catch {
        /* localStorage may be unavailable (Safari private mode, disabled) — best-effort. */
      }
    }
  }, [searchParams]);

  return null;
}

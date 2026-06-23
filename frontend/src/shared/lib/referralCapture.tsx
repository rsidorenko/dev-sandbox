"use client";

import { useEffect } from "react";
import { useSearchParams } from "next/navigation";

/**
 * Invisible client component that captures the `ref` query parameter
 * from the URL and stores it in localStorage for later use during
 * email verification (web referral tracking).
 */
export function ReferralCapture() {
  const searchParams = useSearchParams();

  useEffect(() => {
    const ref = searchParams.get("ref");
    if (ref) {
      localStorage.setItem("pending_referral_code", ref);
    }
  }, [searchParams]);

  return null;
}

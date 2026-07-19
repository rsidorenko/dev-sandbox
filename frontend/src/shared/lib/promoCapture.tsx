"use client";

import { useEffect } from "react";
import { useSearchParams } from "next/navigation";

/**
 * Captures the `promo` query parameter (e.g. from the trial-eve deep-link
 * `/?promo=BRAVADA-xxx`) into localStorage so the checkout page can pre-fill it.
 * Mirrors ReferralCapture.
 */
export function PromoCapture() {
  const searchParams = useSearchParams();

  useEffect(() => {
    const promo = searchParams.get("promo");
    if (promo) {
      localStorage.setItem("pending_promo_code", promo);
    }
  }, [searchParams]);

  return null;
}

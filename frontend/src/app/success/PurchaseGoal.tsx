"use client";

import { useEffect } from "react";
import { reachGoal } from "@/shared/lib/analytics";

/** Фиксирует цель Метрики «purchase» при успешной оплате. */
export function PurchaseGoal() {
  useEffect(() => {
    reachGoal("purchase");
  }, []);
  return null;
}

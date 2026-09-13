import { describe, expect, it } from "vitest";

import type { AdminUser } from "@/entities/admin/types";
import {
  balanceBucket,
  computeFacets,
  emptyFilters,
  filterUsers,
  statusBucket,
  trialBucket,
} from "./userFilters";

function user(over: Partial<AdminUser> = {}): AdminUser {
  return {
    internal_user_id: "u1",
    telegram_user_id: 1,
    email: null,
    trial_used: false,
    is_internal: false,
    subscription_state: null,
    is_active: false,
    active_until: null,
    remaining_days: null,
    plan_id: null,
    device_count: null,
    balance_rubles: 0,
    ...over,
  };
}

describe("бакеты", () => {
  it("status: active / overdue / expired / cancelled / none", () => {
    expect(statusBucket(user({ is_active: true, subscription_state: "active" }))).toBe("active");
    expect(statusBucket(user({ is_active: false, subscription_state: "active" }))).toBe("expired");
    expect(statusBucket(user({ is_active: false, subscription_state: "expired" }))).toBe("expired");
    expect(statusBucket(user({ is_active: false, subscription_state: "cancelled" }))).toBe("cancelled");
    expect(statusBucket(user({ is_active: false, subscription_state: null }))).toBe("none");
  });
  it("balance: 0 / 1-99 / 100+", () => {
    expect(balanceBucket(user({ balance_rubles: 0 }))).toBe("0");
    expect(balanceBucket(user({ balance_rubles: 50 }))).toBe("1-99");
    expect(balanceBucket(user({ balance_rubles: 99.99 }))).toBe("1-99");
    expect(balanceBucket(user({ balance_rubles: 100 }))).toBe("100+");
  });
  it("trial: used / not", () => {
    expect(trialBucket(user({ trial_used: true }))).toBe("used");
    expect(trialBucket(user({ trial_used: false }))).toBe("not");
  });
});

describe("filterUsers", () => {
  const users = [
    user({ internal_user_id: "a", is_active: true, subscription_state: "active", plan_id: "1m", balance_rubles: 50, trial_used: true }),
    user({ internal_user_id: "b", is_active: false, subscription_state: "active", plan_id: "3m", balance_rubles: 0, trial_used: false }),
    user({ internal_user_id: "c", is_active: false, subscription_state: null, plan_id: null, balance_rubles: 150, trial_used: false }),
  ];

  it("без фильтров — все", () => {
    expect(filterUsers(users, emptyFilters())).toHaveLength(3);
  });

  it("OR внутри фасеты: status active+none → a и c", () => {
    const f = emptyFilters();
    f.status.add("active");
    f.status.add("none");
    expect(filterUsers(users, f).map((u) => u.internal_user_id)).toEqual(["a", "c"]);
  });

  it("AND между фасетами: status active И plan 1m → только a", () => {
    const f = emptyFilters();
    f.status.add("active");
    f.plan.add("1m");
    expect(filterUsers(users, f).map((u) => u.internal_user_id)).toEqual(["a"]);
  });

  it("balance 100+ → только c", () => {
    const f = emptyFilters();
    f.balance.add("100+");
    expect(filterUsers(users, f).map((u) => u.internal_user_id)).toEqual(["c"]);
  });
});

describe("computeFacets", () => {
  const users = [
    user({ internal_user_id: "a", is_active: true, subscription_state: "active", plan_id: "1m", balance_rubles: 50 }),
    user({ internal_user_id: "b", is_active: true, subscription_state: "active", plan_id: "3m", balance_rubles: 0 }),
    user({ internal_user_id: "c", is_active: false, subscription_state: null, plan_id: null, balance_rubles: 150 }),
  ];

  it("без фильтров: status active=2/none=1, plan 1m=1/3m=1/none=1", () => {
    const fc = computeFacets(users, emptyFilters());
    const byKey = (facet: keyof typeof fc) => Object.fromEntries(fc[facet].map((o) => [o.key, o.count]));
    expect(byKey("status")).toEqual({ active: 2, none: 1 });
    expect(byKey("plan")).toEqual({ "1m": 1, "3m": 1, none: 1 });
  });

  it("выбор status=active обновляет счётчики plan только активных (1m=1,3m=1, без none)", () => {
    const f = emptyFilters();
    f.status.add("active");
    const fc = computeFacets(users, f);
    const plan = Object.fromEntries(fc.plan.map((o) => [o.key, o.count]));
    expect(plan).toEqual({ "1m": 1, "3m": 1 }); // 'none' (пользователь c) исчез — он не active
  });

  it("пустые значения не показываются", () => {
    const fc = computeFacets(users, emptyFilters());
    expect(fc.balance.map((o) => o.key).includes("1-99")).toBe(true);
    // нет значения с 0 count
    for (const facet of ["status", "plan", "balance", "trial", "devices"] as const) {
      for (const o of fc[facet]) expect(o.count).toBeGreaterThan(0);
    }
  });
});

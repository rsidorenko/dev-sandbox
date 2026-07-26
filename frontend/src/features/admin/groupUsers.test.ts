import { describe, expect, it } from "vitest";

import type { AdminUser } from "@/entities/admin/types";
import { balanceBucket, groupUsers, statusBucket, trialBucket, type UserGrouping } from "./groupUsers";

function user(over: Partial<AdminUser> = {}): AdminUser {
  return {
    internal_user_id: "u1",
    telegram_user_id: 1,
    email: null,
    trial_used: false,
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

describe("statusBucket", () => {
  it("активный при is_active", () => {
    expect(statusBucket(user({ is_active: true, subscription_state: "active" }))).toBe("active");
  });
  it("active-but-overdue → expired", () => {
    expect(statusBucket(user({ is_active: false, subscription_state: "active" }))).toBe("expired");
  });
  it("явный expired → expired", () => {
    expect(statusBucket(user({ is_active: false, subscription_state: "expired" }))).toBe("expired");
  });
  it("cancelled → cancelled", () => {
    expect(statusBucket(user({ is_active: false, subscription_state: "cancelled" }))).toBe("cancelled");
  });
  it("нет снапшота → none", () => {
    expect(statusBucket(user({ is_active: false, subscription_state: null }))).toBe("none");
  });
});

describe("balanceBucket", () => {
  it("0 → '0'", () => expect(balanceBucket(user({ balance_rubles: 0 }))).toBe("0"));
  it("50 → '1-99'", () => expect(balanceBucket(user({ balance_rubles: 50 }))).toBe("1-99"));
  it("99.99 → '1-99'", () => expect(balanceBucket(user({ balance_rubles: 99.99 }))).toBe("1-99"));
  it("100 → '100+'", () => expect(balanceBucket(user({ balance_rubles: 100 }))).toBe("100+"));
  it("отрицательный (виртуальный) → '0'", () => expect(balanceBucket(user({ balance_rubles: -5 }))).toBe("0"));
});

describe("trialBucket", () => {
  it("trial_used=true → used", () => expect(trialBucket(user({ trial_used: true }))).toBe("used"));
  it("trial_used=false → not", () => expect(trialBucket(user({ trial_used: false }))).toBe("not"));
});

describe("groupUsers", () => {
  const users: AdminUser[] = [
    user({ internal_user_id: "a", telegram_user_id: 3, is_active: true, subscription_state: "active", plan_id: "1m", balance_rubles: 50, trial_used: true, device_count: 2 }),
    user({ internal_user_id: "b", telegram_user_id: 2, is_active: false, subscription_state: "active", plan_id: "1m", balance_rubles: 0, trial_used: true, device_count: 5 }),
    user({ internal_user_id: "c", telegram_user_id: 1, is_active: false, subscription_state: null, plan_id: null, balance_rubles: 150, trial_used: false, device_count: null }),
  ];

  it("none → пустой массив", () => {
    expect(groupUsers(users, "none")).toEqual([]);
  });

  it("по статусу: порядок active→expired→none, пропуская cancelled", () => {
    const g = groupUsers(users, "status");
    expect(g.map((x) => x.key)).toEqual(["active", "expired", "none"]);
    expect(g[0].users).toHaveLength(1); // a
    expect(g[1].users).toHaveLength(1); // b (active-but-overdue)
    expect(g[2].users).toHaveLength(1); // c
  });

  it("по тарифу: порядок 1m → Без тарифа", () => {
    const g = groupUsers(users, "plan");
    expect(g.map((x) => x.key)).toEqual(["1m", "none"]);
  });

  it("по балансу: 0 / 1-99 / 100+ в порядке", () => {
    const g = groupUsers(users, "balance");
    expect(g.map((x) => x.key)).toEqual(["0", "1-99", "100+"]);
  });

  it("по триалу: used → not", () => {
    const g = groupUsers(users, "trial");
    expect(g.map((x) => x.key)).toEqual(["used", "not"]);
  });

  it("по устройствам: 2 → 5+ → Нет данных", () => {
    const g = groupUsers(users, "devices");
    expect(g.map((x) => x.key)).toEqual(["2", "5+", "none"]);
  });

  it("sumBalance корректно суммирует по секции", () => {
    const g = groupUsers(
      [user({ balance_rubles: 50.5 }), user({ balance_rubles: 49.5 }), user({ balance_rubles: 0 })],
      "balance",
    );
    const bucket99 = g.find((x) => x.key === "1-99");
    expect(bucket99?.sumBalance).toBe(100);
  });

  it("пропускает пустые бакеты", () => {
    // только активные — expired/cancelled/none не должны появиться
    const g = groupUsers([user({ is_active: true, subscription_state: "active" })], "status");
    expect(g.map((x) => x.key)).toEqual(["active"]);
  });
});

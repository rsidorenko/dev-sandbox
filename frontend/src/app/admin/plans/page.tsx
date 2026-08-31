"use client";

import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { adminApi } from "@/features/admin/api";
import {
  adminBtnPrimary,
  adminInput,
  adminMono,
  adminNum,
  adminRow,
  adminTable,
  adminTd,
  adminTableWrap,
  adminTh,
} from "@/features/admin/ui";
import type { AdminGlobalDiscount, AdminPlanPrice } from "@/entities/admin/types";
import { Alert } from "@/shared/ui/Alert";

// Подписи 7 фиксированных тарифов (зеркало plan_display_name бэкенда).
const PLAN_TITLES: Record<string, string> = {
  "1d": "1 день",
  "7d": "7 дней",
  "14d": "2 недели",
  "1m": "1 месяц",
  "3m": "3 месяца",
  "6m": "6 месяцев",
  "365d": "1 год",
};

const PRICE_MIN = 1;
const PRICE_MAX = 100_000;

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

export default function AdminPlansPage() {
  const [plans, setPlans] = useState<AdminPlanPrice[]>([]);
  const [discount, setDiscount] = useState<AdminGlobalDiscount | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // Черновики цен по plan_id (строки для input); пусто = не редактируется.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busyPlan, setBusyPlan] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const r = await adminApi.listPlans();
    if (r.ok) {
      setPlans(r.data.plans);
      setDiscount(r.data.global_discount);
      setDrafts({});
    } else if (r.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent("/admin/plans")}`;
    } else {
      setError("Не удалось загрузить тарифы.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const draftValue = (planId: string, current: number): number | null => {
    const raw = (drafts[planId] ?? "").trim();
    if (raw === "") return null;
    if (!/^\d+$/.test(raw)) return null;
    const n = Number(raw);
    if (n < PRICE_MIN || n > PRICE_MAX) return null;
    return n;
  };

  const onSavePrice = async (p: AdminPlanPrice) => {
    const price = draftValue(p.plan_id, p.price_rubles);
    if (price === null) {
      setError(`Цена «${PLAN_TITLES[p.plan_id] ?? p.plan_id}»: целое число от ${PRICE_MIN} до ${PRICE_MAX} ₽.`);
      return;
    }
    setBusyPlan(p.plan_id);
    setError("");
    setNotice("");
    const r = await adminApi.setPlanPrice(p.plan_id, price);
    if (!r.ok) {
      setError(r.error === "invalid_price" || r.error === "price_out_of_range"
        ? `Цена должна быть целым числом от ${PRICE_MIN} до ${PRICE_MAX} ₽.`
        : "Не удалось сохранить цену.");
    } else {
      setNotice(`Цена «${PLAN_TITLES[p.plan_id] ?? p.plan_id}» → ${price} ₽. Новые цены уже в боте и на сайте.`);
      void load();
    }
    setBusyPlan(null);
  };

  const onResetPrice = async (p: AdminPlanPrice) => {
    if (!window.confirm(`Сбросить цену «${PLAN_TITLES[p.plan_id] ?? p.plan_id}» к код-дефолту ${p.default_price_rubles} ₽?`))
      return;
    setBusyPlan(p.plan_id);
    setError("");
    setNotice("");
    const r = await adminApi.resetPlanPrice(p.plan_id);
    if (!r.ok) {
      setError(r.error === "no_override" ? "Оверрайд не задан — сбрасывать нечего." : "Не удалось сбросить цену.");
    } else {
      setNotice(`Цена «${PLAN_TITLES[p.plan_id] ?? p.plan_id}» сброшена к ${r.data.price_rubles} ₽.`);
      void load();
    }
    setBusyPlan(null);
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Тарифы</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Цены применяются сразу — в боте, на сайте и в чекауте. «Свой тариф» и доп. устройства не редактируются.
        </p>
      </div>

      {error && <Alert className="mb-4">{error}</Alert>}
      {notice && (
        <div className="mb-4 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-900 dark:bg-green-950/40 dark:text-green-400">
          {notice}
        </div>
      )}

      <div className={adminTableWrap}>
        <table className={adminTable}>
          <thead>
            <tr>
              <th className={adminTh}>Тариф</th>
              <th className={`${adminTh} ${adminNum} text-right`}>Дней</th>
              <th className={`${adminTh} ${adminNum} text-right`}>Цена по коду</th>
              <th className={`${adminTh} ${adminNum} text-right`}>Текущая цена, ₽</th>
              <th className={adminTh}>Изменено</th>
              <th className={`${adminTh} text-right`}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-gray-400">
                  Загрузка…
                </td>
              </tr>
            ) : (
              plans.map((p) => {
                const draft = drafts[p.plan_id] ?? String(p.price_rubles);
                const parsed = draftValue(p.plan_id, p.price_rubles);
                const changed = parsed !== null && parsed !== p.price_rubles;
                return (
                  <tr key={p.plan_id} className={adminRow}>
                    <td className={`${adminTd} font-semibold text-gray-900 dark:text-gray-100`}>
                      {PLAN_TITLES[p.plan_id] ?? p.plan_id}
                      {p.is_overridden && (
                        <span className="ml-2 inline-flex items-center rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">
                          оверрайд
                        </span>
                      )}
                    </td>
                    <td className={`${adminTd} ${adminNum} text-right`}>{p.duration_days}</td>
                    <td className={`${adminTd} ${adminNum} text-right text-gray-400 dark:text-gray-500`}>
                      {p.default_price_rubles}
                    </td>
                    <td className={`${adminTd} text-right`}>
                      <input
                        type="text"
                        inputMode="numeric"
                        value={draft}
                        onChange={(e) => setDrafts((d) => ({ ...d, [p.plan_id]: e.target.value }))}
                        className={`${adminInput} ${adminNum} ${adminMono} w-28 text-right`}
                        aria-label={`Цена тарифа ${PLAN_TITLES[p.plan_id] ?? p.plan_id}`}
                      />
                    </td>
                    <td className={`${adminTd} text-xs text-gray-500 dark:text-gray-400`}>
                      {p.is_overridden ? (
                        <>
                          {fmtDate(p.updated_at)}
                          <span className="block text-gray-400 dark:text-gray-500">{p.updated_by ?? "—"}</span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className={`${adminTd} text-right`}>
                      <div className="flex justify-end gap-3">
                        <button
                          type="button"
                          disabled={!changed || busyPlan === p.plan_id}
                          onClick={() => void onSavePrice(p)}
                          className="text-sm font-semibold text-brand-600 transition hover:text-brand-700 disabled:opacity-40"
                        >
                          Сохранить
                        </button>
                        <button
                          type="button"
                          disabled={!p.is_overridden || busyPlan === p.plan_id}
                          onClick={() => void onResetPrice(p)}
                          className="text-sm font-semibold text-red-600 transition hover:text-red-700 disabled:opacity-40"
                        >
                          Сбросить
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-8">
        {discount && (
          <GlobalDiscountCard
            key={`${discount.is_active}:${discount.discount_kind}:${discount.discount_value ?? ""}`}
            initial={discount}
            onSaved={(gd) => {
              setDiscount(gd);
              setNotice(
                gd.is_active
                  ? "Глобальная скидка включена — отображается всем как акция."
                  : "Глобальная скидка выключена.",
              );
              setError("");
            }}
          />
        )}
      </div>
    </div>
  );
}

// ─── Карточка глобальной скидки ─────────────────────────────────────────────

const gdSchema = z
  .object({
    is_active: z.boolean(),
    discount_kind: z.enum(["percent", "fixed_rubles"]),
    discount_value: z.number().int("Только целое число").min(1, "Минимум 1"),
    public_label: z.string().max(64, "Максимум 64 символа"),
  })
  .superRefine((v, ctx) => {
    if (v.discount_kind === "percent" && v.discount_value > 100) {
      ctx.addIssue({
        code: "custom",
        path: ["discount_value"],
        message: "Процент не больше 100",
      });
    }
  });

type GdValues = z.infer<typeof gdSchema>;

function GlobalDiscountCard({
  initial,
  onSaved,
}: {
  initial: AdminGlobalDiscount;
  onSaved: (gd: AdminGlobalDiscount) => void;
}) {
  const [serverError, setServerError] = useState("");
  const [busy, setBusy] = useState(false);

  const form = useForm<GdValues>({
    resolver: zodResolver(gdSchema),
    defaultValues: {
      is_active: initial.is_active,
      discount_kind: initial.discount_kind ?? "percent",
      discount_value: initial.discount_value ?? 10,
      public_label: initial.public_label ?? "",
    },
  });

  const isActive = form.watch("is_active");
  const kind = form.watch("discount_kind");

  const onSubmit = form.handleSubmit(async (v) => {
    setBusy(true);
    setServerError("");
    const r = await adminApi.setGlobalDiscount({
      is_active: v.is_active,
      discount_kind: v.discount_kind,
      discount_value: v.discount_value,
      public_label: v.public_label.trim() || null,
    });
    if (!r.ok) {
      setServerError(
        r.error === "invalid_discount" || r.error === "invalid_discount_value" || r.error === "invalid_discount_kind"
          ? "Проверьте параметры скидки: процент 1–100, фикс — целое число от 1 ₽."
          : "Не удалось сохранить скидку.",
      );
    } else {
      onSaved(r.data.global_discount);
    }
    setBusy(false);
  });

  const labelClass = "block text-sm font-medium text-gray-700 dark:text-gray-300";

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5 dark:border-zinc-700 dark:bg-zinc-800/60">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">Глобальная скидка</h2>
          <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
            Скидка всем без промокода — на 7 фиксированных тарифов. С промокодом пользователя действует большая из двух.
          </p>
        </div>
        <span
          className={`inline-flex shrink-0 items-center rounded-md px-2 py-0.5 text-xs font-medium ${
            initial.is_active
              ? "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-400"
              : "bg-gray-100 text-gray-500 dark:bg-zinc-700 dark:text-gray-400"
          }`}
        >
          {initial.is_active ? "активна" : "выкл"}
        </span>
      </div>

      {serverError && <Alert className="mb-4">{serverError}</Alert>}

      <form onSubmit={(e) => void onSubmit(e)} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="flex items-center gap-2 self-end pb-2">
          <input
            type="checkbox"
            {...form.register("is_active")}
            className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
          />
          <span className={labelClass}>Включить</span>
        </label>

        <div>
          <span className={labelClass}>Тип</span>
          <select {...form.register("discount_kind")} className={adminInput} disabled={!isActive}>
            <option value="percent">Процент</option>
            <option value="fixed_rubles">Фикс, ₽</option>
          </select>
        </div>

        <div>
          <span className={labelClass}>{kind === "percent" ? "Размер, %" : "Размер, ₽"}</span>
          <input
            type="number"
            min={1}
            max={kind === "percent" ? 100 : undefined}
            {...form.register("discount_value", { valueAsNumber: true })}
            className={adminInput}
            disabled={!isActive}
          />
          {form.formState.errors.discount_value && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">
              {form.formState.errors.discount_value.message}
            </p>
          )}
        </div>

        <div>
          <span className={labelClass}>Подпись на витрине</span>
          <input
            type="text"
            placeholder="напр. Акция"
            {...form.register("public_label")}
            className={adminInput}
            disabled={!isActive}
          />
          {form.formState.errors.public_label && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">{form.formState.errors.public_label.message}</p>
          )}
        </div>

        <div className="sm:col-span-2 lg:col-span-4">
          <button type="submit" disabled={busy} className={adminBtnPrimary}>
            {busy ? "Сохранение…" : "Сохранить скидку"}
          </button>
        </div>
      </form>
      <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
        Выключение сохраняет последние параметры — повторное включение в один клик.
      </p>
    </section>
  );
}

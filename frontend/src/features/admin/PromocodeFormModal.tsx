"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Modal } from "@/shared/ui/Modal";
import { Alert } from "@/shared/ui/Alert";
import { adminApi } from "./api";
import { siteConfig } from "@/shared/config/site";
import type { AdminPromocode, AdminPromocodePayload, PromoAudience, PromoDiscountKind } from "@/entities/admin/types";

// 7 фиксированных тарифов для per-tariff редактора («Свой» исключён).
const FIXED_TARIFFS = siteConfig.tariffs;

const schema = z.object({
  code: z
    .string()
    .min(3, "Минимум 3 символа")
    .max(32, "Максимум 32 символа")
    .regex(/^[A-Za-z0-9]+$/, "Только латиница и цифры"),
  audience: z.enum(["new_users", "existing_users", "all_users"]),
  discount_kind: z.enum(["percent", "fixed_rubles"]),
  // Плоская мода; в per-tariff моде поле не используется (опц. в schema, валидация в onSubmit).
  discount_value: z.number().int().min(1, "Минимум 1").optional(),
  max_redemptions: z
    .string()
    .optional()
    .refine((v) => !v || /^\d+$/.test(v), "Только число"),
  expires_at: z.string().optional(),
  note: z.string().max(500).optional(),
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;
type DiscountMode = "flat" | "perTariff";

const inputClass =
  "mt-1 block w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100";
const labelClass = "block text-sm font-medium text-gray-700 dark:text-gray-300";

function initialMode(initial: AdminPromocode | null): DiscountMode {
  return initial?.discounts_by_plan && Object.keys(initial.discounts_by_plan).length > 0
    ? "perTariff"
    : "flat";
}

export function PromocodeFormModal({
  initial,
  onClose,
  onSaved,
}: {
  initial: AdminPromocode | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [serverError, setServerError] = useState("");
  const [mode, setMode] = useState<DiscountMode>(() => initialMode(initial));
  // per-tariff значения как строки (для input); парсим в onSubmit.
  const [perPlan, setPerPlan] = useState<Record<string, string>>(() => {
    const m = initial?.discounts_by_plan ?? {};
    const out: Record<string, string> = {};
    for (const [pid, v] of Object.entries(m)) out[pid] = String(v);
    return out;
  });
  const isEdit = initial !== null;

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: initial
      ? {
          code: initial.code,
          audience: initial.audience,
          discount_kind: initial.discount_kind,
          discount_value: initial.discount_value || undefined,
          max_redemptions: initial.max_redemptions ? String(initial.max_redemptions) : "",
          expires_at: initial.expires_at ? initial.expires_at.slice(0, 16) : "",
          note: initial.note ?? "",
          is_active: initial.is_active,
        }
      : {
          code: "",
          audience: "new_users",
          discount_kind: "percent",
          discount_value: 10,
          max_redemptions: "",
          expires_at: "",
          note: "",
          is_active: true,
        },
  });

  const buildDiscountPayload = (
    v: FormValues,
  ): { discount_value: number | null; discounts_by_plan: Record<string, number> | null } => {
    if (mode === "perTariff") {
      const map: Record<string, number> = {};
      for (const t of FIXED_TARIFFS) {
        const raw = (perPlan[t.id] ?? "").trim();
        if (raw === "") continue;
        map[t.id] = Number(raw);
      }
      return { discounts_by_plan: map, discount_value: null };
    }
    return { discount_value: v.discount_value ?? 0, discounts_by_plan: null };
  };

  const validateDiscount = (v: FormValues): string | null => {
    if (mode === "perTariff") {
      const entries = FIXED_TARIFFS.map((t) => [t.id, (perPlan[t.id] ?? "").trim()] as const).filter(
        ([, raw]) => raw !== "",
      );
      if (entries.length === 0) return "Укажите скидку хотя бы для одного тарифа.";
      for (const [pid, raw] of entries) {
        const n = Number(raw);
        if (!Number.isInteger(n) || n < 1) return `Скидка для тарифа «${pid}» — целое число ≥ 1.`;
        if (v.discount_kind === "percent" && n > 100) return `Процент для тарифа «${pid}» не может превышать 100.`;
      }
      return null;
    }
    if (!v.discount_value || v.discount_value < 1) return "Значение скидки — целое ≥ 1.";
    if (v.discount_kind === "percent" && v.discount_value > 100) return "Процент не может превышать 100.";
    return null;
  };

  const onSubmit = form.handleSubmit(async (v) => {
    setServerError("");
    const err = validateDiscount(v);
    if (err) {
      setServerError(err);
      return;
    }
    const discount = buildDiscountPayload(v);
    const payload: AdminPromocodePayload = {
      code: v.code.trim().toUpperCase(),
      audience: v.audience as PromoAudience,
      discount_kind: v.discount_kind as PromoDiscountKind,
      discount_value: discount.discount_value,
      discounts_by_plan: discount.discounts_by_plan,
      max_redemptions: v.max_redemptions && v.max_redemptions.trim() ? Number(v.max_redemptions.trim()) : null,
      expires_at: v.expires_at ? new Date(v.expires_at).toISOString() : null,
      note: v.note?.trim() ? v.note.trim() : null,
      is_active: v.is_active,
    };

    if (isEdit && initial) {
      const { code: _code, ...rest } = payload;
      void _code;
      const r = await adminApi.updatePromocode(initial.code, rest);
      if (!r.ok) {
        setServerError(mapError(r.error));
        return;
      }
    } else {
      const r = await adminApi.createPromocode(payload);
      if (!r.ok) {
        setServerError(mapError(r.error));
        return;
      }
    }
    onSaved();
    onClose();
  });

  const discountUnit = form.watch("discount_kind") === "percent" ? "%" : "₽";

  return (
    <Modal onClose={onClose}>
      <div className="w-full max-w-lg rounded-2xl border border-gray-100 bg-white p-6 shadow-xl dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">
          {isEdit ? "Редактировать промокод" : "Новый промокод"}
        </h2>

        {serverError && <Alert className="mt-4">{serverError}</Alert>}

        <form onSubmit={onSubmit} className="mt-4 space-y-4">
          <div>
            <label className={labelClass} htmlFor="code">
              Код
            </label>
            <input
              id="code"
              className={inputClass}
              disabled={isEdit || form.formState.isSubmitting}
              {...form.register("code")}
              placeholder="WELCOME"
            />
            {form.formState.errors.code && (
              <p className="mt-1 text-xs text-red-600">{form.formState.errors.code.message}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="audience">
                Для кого
              </label>
              <select id="audience" className={inputClass} {...form.register("audience")}>
                <option value="new_users">Только новые</option>
                <option value="existing_users">Только не новые</option>
                <option value="all_users">Все пользователи</option>
              </select>
            </div>
            <div>
              <label className={labelClass} htmlFor="discount_kind">
                Тип скидки
              </label>
              <select id="discount_kind" className={inputClass} {...form.register("discount_kind")}>
                <option value="percent">Процент (%)</option>
                <option value="fixed_rubles">Фикс. (руб)</option>
              </select>
            </div>
          </div>

          {/* Мода скидки: единая или по тарифам */}
          <div>
            <label className={labelClass}>Скидка</label>
            <div className="mt-1 inline-flex rounded-lg border border-gray-200 p-0.5 dark:border-zinc-600">
              <button
                type="button"
                onClick={() => setMode("flat")}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  mode === "flat"
                    ? "bg-brand-600 text-white"
                    : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-zinc-700"
                }`}
              >
                Единая
              </button>
              <button
                type="button"
                onClick={() => setMode("perTariff")}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  mode === "perTariff"
                    ? "bg-brand-600 text-white"
                    : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-zinc-700"
                }`}
              >
                По тарифам
              </button>
            </div>

            {mode === "flat" ? (
              <div className="mt-2">
                <input
                  id="discount_value"
                  type="number"
                  min={1}
                  className={inputClass}
                  disabled={form.formState.isSubmitting}
                  {...form.register("discount_value", {
                    setValueAs: (val) => (val === "" || val == null ? Number.NaN : Number(val)),
                  })}
                />
                {form.formState.errors.discount_value && (
                  <p className="mt-1 text-xs text-red-600">{form.formState.errors.discount_value.message}</p>
                )}
              </div>
            ) : (
              <div className="mt-2 grid grid-cols-2 gap-2">
                {FIXED_TARIFFS.map((t) => (
                  <div key={t.id}>
                    <label htmlFor={`plan-${t.id}`} className="mb-0.5 block text-xs text-gray-500 dark:text-gray-400">
                      {t.label}
                    </label>
                    <input
                      id={`plan-${t.id}`}
                      type="number"
                      min={1}
                      placeholder={`— ${discountUnit}`}
                      className={`${inputClass} mt-0`}
                      value={perPlan[t.id] ?? ""}
                      onChange={(e) => setPerPlan((prev) => ({ ...prev, [t.id]: e.target.value }))}
                    />
                  </div>
                ))}
                <p className="col-span-2 mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Пустые поля = нет скидки на этот тариф. «Свой» тариф не настраивается (всегда полная цена).
                </p>
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="max_redemptions">
                Лимит погашений (пусто = ∞)
              </label>
              <input id="max_redemptions" type="number" min={1} className={inputClass} {...form.register("max_redemptions")} />
            </div>
            <div>
              <label className={labelClass} htmlFor="expires_at">
                Срок действия (до)
              </label>
              <input id="expires_at" type="datetime-local" className={inputClass} {...form.register("expires_at")} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="note">
                Заметка (опц.)
              </label>
              <input id="note" className={inputClass} {...form.register("note")} />
            </div>
            <div className="flex items-end pb-2">
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" className="h-4 w-4" {...form.register("is_active")} />
                Активен
              </label>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-gray-300 dark:hover:bg-zinc-700"
            >
              Отмена
            </button>
            <button
              type="submit"
              disabled={form.formState.isSubmitting}
              className="rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
            >
              {form.formState.isSubmitting ? "Сохранение..." : "Сохранить"}
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}

function mapError(error: string): string {
  switch (error) {
    case "promo_code_exists":
      return "Промокод с таким кодом уже существует.";
    case "discount_mode_conflict":
      return "Передайте либо единое значение, либо скидки по тарифам — не оба сразу.";
    case "invalid_code":
    case "invalid_discount_value":
    case "invalid_discounts_by_plan":
    case "invalid_kind":
    case "invalid_max_redemptions":
    case "invalid_expires_at":
      return "Проверьте корректность полей (код, тип/значение скидки, лимит, срок).";
    case "promo_code_not_found":
      return "Промокод не найден (возможно, был удалён).";
    default:
      return "Не удалось сохранить. Попробуйте позже.";
  }
}

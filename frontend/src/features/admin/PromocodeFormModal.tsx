"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Modal } from "@/shared/ui/Modal";
import { Alert } from "@/shared/ui/Alert";
import { adminApi } from "./api";
import type { AdminPromocode, AdminPromocodePayload, PromoAudience, PromoDiscountKind } from "@/entities/admin/types";

const schema = z.object({
  code: z
    .string()
    .min(3, "Минимум 3 символа")
    .max(32, "Максимум 32 символа")
    .regex(/^[A-Za-z0-9]+$/, "Только латиница и цифры"),
  audience: z.enum(["new_users", "existing_users"]),
  discount_kind: z.enum(["percent", "fixed_rubles"]),
  // discount_value — number; конвертация строки из input идёт через setValueAs в register.
  discount_value: z.number().int().min(1, "Минимум 1"),
  max_redemptions: z
    .string()
    .optional()
    .refine((v) => !v || /^\d+$/.test(v), "Только число"),
  expires_at: z.string().optional(),
  note: z.string().max(500).optional(),
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

const inputClass =
  "mt-1 block w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100";
const labelClass = "block text-sm font-medium text-gray-700 dark:text-gray-300";

function payloadFromValues(v: FormValues): AdminPromocodePayload {
  return {
    code: v.code.trim().toUpperCase(),
    audience: v.audience as PromoAudience,
    discount_kind: v.discount_kind as PromoDiscountKind,
    discount_value: v.discount_value,
    max_redemptions: v.max_redemptions && v.max_redemptions.trim() ? Number(v.max_redemptions.trim()) : null,
    expires_at: v.expires_at ? new Date(v.expires_at).toISOString() : null,
    note: v.note?.trim() ? v.note.trim() : null,
    is_active: v.is_active,
  };
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
  const isEdit = initial !== null;

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: initial
      ? {
          code: initial.code,
          audience: initial.audience,
          discount_kind: initial.discount_kind,
          discount_value: initial.discount_value,
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

  const onSubmit = form.handleSubmit(async (v) => {
    setServerError("");
    if (isEdit && initial) {
      const { code, ...rest } = payloadFromValues(v);
      void code;
      const r = await adminApi.updatePromocode(initial.code, rest);
      if (!r.ok) {
        setServerError(mapError(r.error));
        return;
      }
    } else {
      const r = await adminApi.createPromocode(payloadFromValues(v));
      if (!r.ok) {
        setServerError(mapError(r.error));
        return;
      }
    }
    onSaved();
    onClose();
  });

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

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="discount_value">
                Значение скидки
              </label>
              <input
                id="discount_value"
                type="number"
                min={1}
                className={inputClass}
                {...form.register("discount_value", {
                  setValueAs: (v) => (v === "" || v == null ? Number.NaN : Number(v)),
                })}
              />
              {form.formState.errors.discount_value && (
                <p className="mt-1 text-xs text-red-600">{form.formState.errors.discount_value.message}</p>
              )}
            </div>
            <div>
              <label className={labelClass} htmlFor="max_redemptions">
                Лимит погашений (пусто = ∞)
              </label>
              <input id="max_redemptions" type="number" min={1} className={inputClass} {...form.register("max_redemptions")} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="expires_at">
                Срок действия (до)
              </label>
              <input id="expires_at" type="datetime-local" className={inputClass} {...form.register("expires_at")} />
            </div>
            <div className="flex items-end pb-2">
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input type="checkbox" className="h-4 w-4" {...form.register("is_active")} />
                Активен
              </label>
            </div>
          </div>

          <div>
            <label className={labelClass} htmlFor="note">
              Заметка (опц.)
            </label>
            <input id="note" className={inputClass} {...form.register("note")} />
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
    case "invalid_code":
    case "invalid_discount_value":
    case "invalid_kind":
    case "invalid_max_redemptions":
    case "invalid_expires_at":
      return "Проверьте корректность полей.";
    case "promo_code_not_found":
      return "Промокод не найден (возможно, был удалён).";
    default:
      return "Не удалось сохранить. Попробуйте позже.";
  }
}

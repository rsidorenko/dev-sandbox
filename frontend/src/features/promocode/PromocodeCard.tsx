"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { PROMO_ERROR_MESSAGES } from "./api";
import { usePromocode } from "./usePromocode";
import { Alert } from "@/shared/ui/Alert";

const codeSchema = z.object({
  code: z
    .string()
    .min(3, "От 3 символов")
    .max(32, "До 32 символов")
    .regex(/^[A-Za-z0-9]+$/, "Только латиница и цифры"),
});
type CodeForm = z.infer<typeof codeSchema>;

/** Секция «Промокод» личного кабинета: показать закреплённый (код + скидка + срок)
 * либо поле ввода с «Применить». Скидка считается на сервере (apply_promo_discount). */
export function PromocodeCard() {
  const { applied, apply, clear } = usePromocode();
  const [error, setError] = useState("");
  const [channelSubscriptionRequired, setChannelSubscriptionRequired] = useState(false);
  const [clearing, setClearing] = useState(false);
  const form = useForm<CodeForm>({ resolver: zodResolver(codeSchema), defaultValues: { code: "" } });

  const onApply = form.handleSubmit(async ({ code }) => {
    setError("");
    setChannelSubscriptionRequired(false);
    const result = await apply(code.trim().toUpperCase());
    if (!result.ok) {
      setError(PROMO_ERROR_MESSAGES[result.error] ?? "Не удалось применить промокод. Попробуйте позже.");
      setChannelSubscriptionRequired(result.error === "channel_subscription_required");
      return;
    }
    form.reset({ code: "" });
  });

  const onClear = async () => {
    setClearing(true);
    setError("");
    await clear();
    setClearing(false);
  };

  return (
    <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Промокод</h2>

      {applied ? (
        <div className="mt-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-lg font-bold text-gray-900 dark:text-gray-100">
              🎁 {applied.code}
            </span>
            <span className="rounded-full bg-brand-50 px-3 py-0.5 text-sm font-semibold text-brand-700 dark:bg-brand-900/30 dark:text-brand-300">
              {applied.discount_label}
            </span>
          </div>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Скидка действует ещё {applied.remaining_hours} ч и будет применена автоматически при
            оплате подписки или устройств.
          </p>
          <button
            type="button"
            onClick={onClear}
            disabled={clearing}
            className="mt-3 text-sm font-medium text-gray-500 transition hover:text-gray-700 disabled:opacity-50 dark:text-gray-400 dark:hover:text-gray-200"
          >
            {clearing ? "Убираю…" : "Убрать промокод"}
          </button>
        </div>
      ) : (
        <form onSubmit={onApply} className="mt-3 space-y-3">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Введите промокод — скидка закрепится на 24 часа.
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              autoCapitalize="characters"
              autoCorrect="off"
              placeholder="Например, WELCOME"
              {...form.register("code")}
              className="block w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm uppercase transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100"
              disabled={form.formState.isSubmitting}
            />
            <button
              type="submit"
              disabled={form.formState.isSubmitting}
              className="shrink-0 rounded-xl bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
            >
              {form.formState.isSubmitting ? "Проверка…" : "Применить"}
            </button>
          </div>
          {form.formState.errors.code && (
            <p className="text-xs text-red-600 dark:text-red-400">{form.formState.errors.code.message}</p>
          )}
          {error && (
            <Alert className="mt-1">
              {error}
              {channelSubscriptionRequired && (
                <>
                  {" "}
                  <a
                    href="https://t.me/bravada_vpn"
                    target="_blank"
                    rel="noreferrer"
                    className="font-semibold underline"
                  >
                    Подписаться на канал
                  </a>
                </>
              )}
            </Alert>
          )}
        </form>
      )}
    </div>
  );
}

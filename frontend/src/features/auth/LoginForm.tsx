"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { authApi } from "./api";
import { siteConfig } from "@/shared/config/site";
import { useCountdown } from "@/shared/hooks/useCountdown";
import { Alert } from "@/shared/ui/Alert";

const emailSchema = z.object({
  email: z.string().min(1, "Введите email").email("Введите корректный email"),
});
type EmailForm = z.infer<typeof emailSchema>;

const codeSchema = z.object({
  code: z
    .string()
    .min(4, "Введите код из письма")
    .max(6)
    .regex(/^\d+$/, "Только цифры"),
});
type CodeForm = z.infer<typeof codeSchema>;

type Step = "email" | "code";

export function LoginForm() {
  const searchParams = useSearchParams();
  const [step, setStep] = useState<Step>("email");
  const [serverError, setServerError] = useState("");
  const [codeTtl, setCodeTtl] = useState(10);
  const { seconds: resendTimer, start: startResendTimer } = useCountdown();

  /* ---------- Email step ---------- */
  const emailForm = useForm<EmailForm>({
    resolver: zodResolver(emailSchema),
    defaultValues: { email: "" },
  });

  const onSendCode = emailForm.handleSubmit(async ({ email }) => {
    setServerError("");
    const result = await authApi.sendCode(email);
    if (!result.ok) {
      if (result.error === "disposable_email") {
        setServerError(
          "Регистрация с временной почтой недоступна. Используйте постоянный email (Gmail, Яндекс, Mail.ru и т. п.).",
        );
      } else if (result.error === "rate_limited") {
        setServerError("Слишком много попыток. Попробуйте позже.");
      } else {
        setServerError("Не удалось отправить код. Попробуйте позже.");
      }
      return;
    }
    setCodeTtl(result.data.ttl_minutes || 10);
    setStep("code");
    startResendTimer(60);
  });

  /* ---------- Code step ---------- */
  const codeForm = useForm<CodeForm>({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: "" },
  });

  const onVerifyCode = codeForm.handleSubmit(async ({ code }) => {
    setServerError("");
    const email = emailForm.getValues("email");
    const referralCode = typeof window !== "undefined"
      ? localStorage.getItem("pending_referral_code") || undefined
      : undefined;
    const result = await authApi.verifyCode(email, code, referralCode);
    if (!result.ok) {
      if (result.error === "invalid_code") {
        setServerError("Неверный код. Попробуйте снова.");
      } else if (result.error === "code_expired") {
        setServerError("Код истёк. Запросите новый.");
        setStep("email");
      } else if (result.error === "email_not_linked") {
        setServerError(
          "Этот email не привязан к аккаунту. Сначала привяжите его через Telegram-бота.",
        );
      } else if (result.error === "too_many_attempts") {
        setServerError("Слишком много попыток. Запросите новый код.");
        setStep("email");
      } else {
        setServerError("Не удалось войти. Попробуйте позже.");
      }
      return;
    }
    if (typeof window !== "undefined") {
      localStorage.removeItem("pending_referral_code");
    }
    const next = searchParams.get("next") || "/dashboard";
    window.location.href = next;
  });

  const handleResend = async () => {
    if (resendTimer > 0) return;
    setServerError("");
    const email = emailForm.getValues("email");
    const result = await authApi.sendCode(email);
    if (result.ok) {
      startResendTimer(60);
    } else {
      setServerError("Не удалось отправить код повторно.");
    }
  };

  return (
    <section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="mx-auto w-full max-w-md px-4">
        <div className="rounded-2xl border border-gray-100 bg-white p-8 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            Вход в аккаунт
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Введите email, привязанный к вашему аккаунту
          </p>

          {serverError && <Alert className="mt-4">{serverError}</Alert>}

          {step === "email" && (
            <form onSubmit={onSendCode} className="mt-6 space-y-4">
              <div>
                <label
                  htmlFor="email"
                  className="block text-sm font-medium text-gray-700 dark:text-gray-300"
                >
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  {...emailForm.register("email")}
                  placeholder="email@example.com"
                  className="mt-1 block w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100"
                  disabled={emailForm.formState.isSubmitting}
                  autoFocus
                />
                {emailForm.formState.errors.email && (
                  <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                    {emailForm.formState.errors.email.message}
                  </p>
                )}
              </div>
              <button
                type="submit"
                disabled={emailForm.formState.isSubmitting}
                className="w-full rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
              >
                {emailForm.formState.isSubmitting
                  ? "Отправка..."
                  : "Получить код"}
              </button>
            </form>
          )}

          {step === "code" && (
            <form onSubmit={onVerifyCode} className="mt-6 space-y-4">
              <div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Код подтверждения отправлен на{" "}
                  <strong>{emailForm.getValues("email")}</strong>
                </p>
              </div>
              <div>
                <label
                  htmlFor="code"
                  className="block text-sm font-medium text-gray-700 dark:text-gray-300"
                >
                  Код подтверждения
                </label>
                <input
                  id="code"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  {...codeForm.register("code")}
                  placeholder="123456"
                  className="mt-1 block w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-center text-lg tracking-widest text-gray-900 transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100"
                  disabled={codeForm.formState.isSubmitting}
                  autoFocus
                />
                {codeForm.formState.errors.code && (
                  <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                    {codeForm.formState.errors.code.message}
                  </p>
                )}
              </div>
              <button
                type="submit"
                disabled={codeForm.formState.isSubmitting}
                className="w-full rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
              >
                {codeForm.formState.isSubmitting ? "Проверка..." : "Войти"}
              </button>
              <div className="flex items-center justify-between text-sm">
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={resendTimer > 0 || codeForm.formState.isSubmitting}
                  className="text-brand-600 transition hover:text-brand-700 disabled:text-gray-400"
                >
                  {resendTimer > 0
                    ? `Отправить повторно (${resendTimer}с)`
                    : "Отправить код повторно"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStep("email");
                    codeForm.reset();
                    setServerError("");
                  }}
                  className="text-gray-500 transition hover:text-gray-700"
                >
                  Изменить email
                </button>
              </div>
              <p className="text-xs text-gray-400">
                Код действителен {codeTtl} минут
              </p>
            </form>
          )}

          <div className="mt-6 border-t border-gray-100 pt-4 text-center text-sm text-gray-500 dark:border-zinc-700">
            <p>
              Нет аккаунта? Привяжите email через{" "}
              <a
                href={`https://t.me/${siteConfig.botUsername}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-brand-600 hover:text-brand-700"
              >
                Telegram-бота
              </a>
            </p>
          </div>

          <div className="mt-4 text-center">
            <Link
              href="/"
              className="text-sm font-medium text-brand-600 transition hover:text-brand-700"
            >
              &larr; На главную
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

export function LoginPage() {
  return (
    <Suspense
      fallback={
        <section className="flex min-h-[70vh] items-center justify-center">
          <p className="text-gray-500">Загрузка...</p>
        </section>
      }
    >
      <LoginForm />
    </Suspense>
  );
}

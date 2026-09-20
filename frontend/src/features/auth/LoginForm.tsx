"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { authApi } from "./api";
import { userApi } from "@/entities/user/api";
import { clearToken, parseLoginToken, setToken } from "@/shared/lib/tokenStore";
import { siteConfig } from "@/shared/config/site";
import { safeNextPath } from "@/shared/lib/safeRedirect";
import { clearPendingReferralCode, readPendingReferralCode } from "@/shared/lib/referralCapture";
import { Alert } from "@/shared/ui/Alert";

const linkSchema = z.object({
  link: z.string().min(1, "Вставьте ссылку или токен"),
});
type LinkForm = z.infer<typeof linkSchema>;

const emailSchema = z.object({
  email: z.string().min(1, "Введите email").email("Введите корректный email"),
});
type EmailForm = z.infer<typeof emailSchema>;

type Mode = "link" | "email";

const EMAIL_ERROR_MESSAGES: Record<string, string> = {
  disposable_email:
    "Регистрация с временной почтой недоступна. Используйте постоянный email (Gmail, Яндекс, Mail.ru и т. п.).",
  rate_limited: "Слишком много попыток. Попробуйте позже.",
  invalid_email: "Введите корректный email.",
  email_not_configured: "Сервис почты временно недоступен. Попробуйте позже.",
};

export function LoginForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("link");
  const [serverError, setServerError] = useState("");
  const [checkingLink, setCheckingLink] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const next = safeNextPath(searchParams.get("next"));

  /* ---------- Вход по ссылке (основной путь) ---------- */
  const linkForm = useForm<LinkForm>({
    resolver: zodResolver(linkSchema),
    defaultValues: { link: "" },
  });

  const onLoginByLink = linkForm.handleSubmit(async ({ link }) => {
    setServerError("");
    const token = parseLoginToken(link);
    if (!token) {
      setServerError(
        "Это не похоже на ссылку Bravada. Скопируйте ссылку целиком из письма или от бота.",
      );
      return;
    }
    setCheckingLink(true);
    // Токен в память → профиль с Bearer валидирует его на бэкенде.
    setToken(token);
    const result = await userApi.profile();
    setCheckingLink(false);
    if (!result.ok) {
      clearToken();
      setServerError(
        "Ссылка недействительна или отозвана. Запросите новую на почту или привяжите аккаунт в Telegram-боте.",
      );
      return;
    }
    // Только клиентская навигация: полная перезагрузка теряет токен в памяти.
    router.push(next);
  });

  /* ---------- Заявка ссылки на почту (новые пользователи) ---------- */
  const emailForm = useForm<EmailForm>({
    resolver: zodResolver(emailSchema),
    defaultValues: { email: "" },
  });

  const onSendLink = emailForm.handleSubmit(async ({ email }) => {
    setServerError("");
    const referralCode = readPendingReferralCode() || undefined;
    const result = await authApi.requestLink(email, referralCode, next);
    if (!result.ok) {
      setServerError(
        EMAIL_ERROR_MESSAGES[result.error] ??
          "Не удалось отправить письмо. Попробуйте позже.",
      );
      return;
    }
    // Реферальный код потреблён при создании аккаунта на бэкенде.
    clearPendingReferralCode();
    setSentTo(email);
  });

  return (
    <section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="mx-auto w-full max-w-md px-4">
        <div className="rounded-2xl border border-gray-100 bg-white p-8 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            Вход в аккаунт
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Вход — по ссылке-подписке. Куки и пароли не используем.
          </p>

          {serverError && <Alert className="mt-4">{serverError}</Alert>}

          {sentTo ? (
            <div className="mt-6 space-y-4">
              <div className="rounded-xl bg-brand-50 p-4 text-center dark:bg-brand-900/20">
                <p className="text-sm font-semibold text-brand-900 dark:text-brand-200">
                  Проверьте почту
                </p>
                <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
                  Мы отправили ссылку для входа на{" "}
                  <strong>{sentTo}</strong>. Откройте письмо и перейдите по
                  ссылке — вернём вас сюда для оплаты.
                </p>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Ссылка — единственный способ входа: почта нигде не сохраняется,
                восстановить потерянную ссылку нельзя. Сохраните письмо или
                привяжите ссылку к Telegram-аккаунту в боте.
              </p>
            </div>
          ) : (
            <>
              <div className="mt-6 grid grid-cols-2 gap-1 rounded-xl bg-gray-100 p-1 dark:bg-zinc-700/60">
                <button
                  type="button"
                  onClick={() => {
                    setMode("link");
                    setServerError("");
                  }}
                  className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
                    mode === "link"
                      ? "bg-white text-gray-900 shadow-sm dark:bg-zinc-800 dark:text-gray-100"
                      : "text-gray-500 hover:text-gray-700 dark:text-zinc-400"
                  }`}
                >
                  По ссылке
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMode("email");
                    setServerError("");
                  }}
                  className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
                    mode === "email"
                      ? "bg-white text-gray-900 shadow-sm dark:bg-zinc-800 dark:text-gray-100"
                      : "text-gray-500 hover:text-gray-700 dark:text-zinc-400"
                  }`}
                >
                  Получить на почту
                </button>
              </div>

              {mode === "link" && (
                <form onSubmit={onLoginByLink} className="mt-6 space-y-4">
                  <div>
                    <label
                      htmlFor="login-link"
                      className="block text-sm font-medium text-gray-700 dark:text-gray-300"
                    >
                      Ссылка или токен подписки
                    </label>
                    <input
                      id="login-link"
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      {...linkForm.register("link")}
                      placeholder="https://bravada-connect.ru/sub/…"
                      className="mt-1 block w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100"
                      disabled={checkingLink}
                      autoFocus
                    />
                    {linkForm.formState.errors.link && (
                      <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                        {linkForm.formState.errors.link.message}
                      </p>
                    )}
                  </div>
                  <button
                    type="submit"
                    disabled={checkingLink}
                    className="w-full rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
                  >
                    {checkingLink ? "Проверка..." : "Войти"}
                  </button>
                  <p className="text-xs text-gray-400">
                    Ссылка есть в письме при покупке и в Telegram-боте
                    («Моя подписка»).
                  </p>
                </form>
              )}

              {mode === "email" && (
                <form onSubmit={onSendLink} className="mt-6 space-y-4">
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
                      autoComplete="email"
                      {...emailForm.register("email")}
                      placeholder="email@example.com"
                      className="mt-1 block w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100"
                      disabled={emailForm.formState.isSubmitting}
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
                      : "Получить ссылку"}
                  </button>
                  <p className="text-xs text-gray-400">
                    Почта используется только для доставки ссылки и не
                    сохраняется.
                  </p>
                </form>
              )}
            </>
          )}

          <div className="mt-6 border-t border-gray-100 pt-4 text-center text-sm text-gray-500 dark:border-zinc-700">
            <p>
              Нет ссылки? Оформите подписку через{" "}
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

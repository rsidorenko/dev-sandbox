"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/shared/lib/auth";
import { trialApi } from "@/entities/trial/api";
import { siteConfig } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";

type Status =
  | "loading" // проверяем вход
  | "activating" // активируем пробный период
  | "success"
  | "already_used"
  | "already_subscribed"
  | "error";

const TG_FALLBACK = `https://t.me/${siteConfig.botUsername}?start=trial`;

export default function TrialPage() {
  const { loading, authenticated, refresh } = useAuth();
  const router = useRouter();
  const [status, setStatus] = useState<Status>("loading");
  const started = useRef(false);

  // Шаг 1: дождаться загрузки auth. Если не авторизован — на вход с возвратом сюда.
  useEffect(() => {
    if (loading) return;
    if (!authenticated) {
      router.replace("/login?next=/trial");
      return;
    }
    setStatus("activating");
    reachGoal("trial_click");
  }, [loading, authenticated, router]);

  // Шаг 2: после авторизации — одна попытка активации (guard от StrictMode double-fire).
  useEffect(() => {
    if (status !== "activating" || started.current) return;
    started.current = true;
    let cancelled = false;
    (async () => {
      const res = await trialApi.activate();
      if (cancelled) return;
      if (res.ok) {
        setStatus("success");
        reachGoal("trial_activated");
        await refresh();
      } else if (res.error === "trial_already_used") {
        setStatus("already_used");
      } else if (res.error === "already_subscribed") {
        setStatus("already_subscribed");
      } else {
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [status, refresh]);

  return (
    <section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="mx-auto w-full max-w-md px-4">
        <div className="rounded-2xl border border-gray-100 bg-white p-8 text-center shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          {(status === "loading" || status === "activating") && (
            <>
              <div className="mx-auto h-10 w-10 animate-spin rounded-full border-4 border-brand-200 border-t-brand-600" />
              <h1 className="mt-5 text-xl font-bold text-gray-900 dark:text-gray-100">
                {status === "loading" ? "Проверяем вход…" : "Активируем пробный период…"}
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Создаём защищённое соединение и ключи доступа
              </p>
            </>
          )}

          {status === "success" && (
            <>
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-2xl dark:bg-emerald-900/40">
                ✅
              </div>
              <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
                Готово! {siteConfig.trialDays} дня активны
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Ключи и ссылка подписки — в личном кабинете.
              </p>
              <div className="mt-6 flex flex-col gap-2">
                <Link
                  href="/dashboard"
                  className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
                >
                  Открыть ключи
                </Link>
                <Link
                  href="/instructions"
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  Как подключиться
                </Link>
              </div>
            </>
          )}

          {(status === "already_used" || status === "already_subscribed") && (
            <>
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-2xl dark:bg-amber-900/40">
                ⏳
              </div>
              <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
                {status === "already_subscribed"
                  ? "У вас уже есть активная подписка"
                  : "Пробный период уже использован"}
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Выберите тариф, чтобы продолжить пользоваться сервисом.
              </p>
              <div className="mt-6 flex flex-col gap-2">
                <Link
                  href="/#tariffs"
                  className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
                >
                  Выбрать тариф
                </Link>
                <Link
                  href="/dashboard"
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  В личный кабинет
                </Link>
              </div>
            </>
          )}

          {status === "error" && (
            <>
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-2xl dark:bg-red-900/40">
                ⚠️
              </div>
              <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
                Не удалось активировать пробный период
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Что-то пошло не так на стороне сервера. Попробуйте ещё раз или
                активируйте через Telegram-бота.
              </p>
              <div className="mt-6 flex flex-col gap-2">
                <button
                  onClick={() => {
                    started.current = false;
                    setStatus("activating");
                  }}
                  className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
                >
                  Повторить
                </button>
                <a
                  href={TG_FALLBACK}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  Через Telegram-бота
                </a>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

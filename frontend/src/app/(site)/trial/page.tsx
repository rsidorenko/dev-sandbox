"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  | "channel_subscription_required"
  | "error";

const TG_FALLBACK = `https://t.me/${siteConfig.botUsername}?start=trial`;
const CHANNEL_URL = "https://t.me/bravada_vpn";

export default function TrialPage() {
  const { loading, authenticated, profile, refresh } = useAuth();
  const router = useRouter();
  const [status, setStatus] = useState<Status>("loading");
  // Фиксируем, что решение по активации уже принято. После этого не уходим в
  // /login и не переоцениваем статус, даже если refresh() на мгновение
  // переключит loading/authenticated — именно это раньше вызывало бесконечный
  // лоадер (refresh → loading флапал → отдельный эффект сбрасывал статус).
  const resolved = useRef(false);

  const runActivation = useCallback(async () => {
    setStatus("activating");
    reachGoal("trial_click");
    const res = await trialApi.activate();
    if (res.ok) {
      setStatus("success");
      reachGoal("trial_activated");
      void refresh(); // обновить профиль, чтобы ЛК показал активный триал
    } else if (res.error === "trial_already_used") {
      setStatus("already_used");
    } else if (res.error === "already_subscribed") {
      setStatus("already_subscribed");
    } else if (res.error === "channel_subscription_required") {
      setStatus("channel_subscription_required");
    } else if (res.error === "unauthorized") {
      // Сессия истекла — на вход с возвратом сюда. Сохраняем ?ref= для атрибуции.
      const ref = new URLSearchParams(window.location.search).get("ref");
      router.replace(`/login?next=/trial${ref ? `&ref=${encodeURIComponent(ref)}` : ""}`);
    } else {
      setStatus("error");
    }
  }, [refresh, router]);

  useEffect(() => {
    if (loading) return;
    // Не авторизован и ещё не начинали — на вход с возвратом сюда. Сохраняем ?ref=.
    if (!authenticated && !resolved.current) {
      const ref = new URLSearchParams(window.location.search).get("ref");
      router.replace(`/login?next=/trial${ref ? `&ref=${encodeURIComponent(ref)}` : ""}`);
      return;
    }
    // Уже приняли решение — повторно не дёргаем (refresh()/флапы auth не мешают).
    if (resolved.current) return;
    resolved.current = true;

    // Предпроверка: подписка уже активна или пробник уже использован — не делаем
    // лишний POST, сразу показываем нужное состояние.
    const sub = profile?.subscription;
    if (sub?.state === "active") {
      setStatus("already_subscribed");
      return;
    }
    if (sub && sub.trial_available === false) {
      setStatus("already_used");
      return;
    }

    void runActivation();
  }, [loading, authenticated, profile, router, runActivation]);

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

          {status === "channel_subscription_required" && (
            <>
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-100 text-2xl dark:bg-brand-900/40">
                🎁
              </div>
              <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
                Пробный период — для подписчиков канала
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Подпишитесь на наш Telegram-канал, затем вернитесь сюда и повторите проверку.
              </p>
              <div className="mt-6 flex flex-col gap-2">
                <a
                  href={CHANNEL_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
                >
                  Подписаться на канал
                </a>
                <button
                  onClick={() => void runActivation()}
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  Я подписался — проверить
                </button>
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
                  onClick={() => void runActivation()}
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

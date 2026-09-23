import type { Metadata } from "next";
import Link from "next/link";
import { siteConfig } from "@/shared/config/site";
import { BotTrialButton } from "./BotTrialButton";

export const metadata: Metadata = {
  title: "Пробный период — Bravada",
  description:
    "Бесплатный пробный период на 3 дня выдаётся в Telegram-боте Bravada: ключи и ссылка для автонастройки приходят сразу после нажатия кнопки.",
};

/**
 * Статичная страница-заглушка: пробный период выдаётся ТОЛЬКО в Telegram-боте.
 * Никакой авто-активации и обращений к API — посторонний путь web-активации
 * триала удалён (privacy/anti-abuse решение, 2026-09).
 */
export default function TrialPage() {
  return (
    <section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="mx-auto w-full max-w-md px-4">
        <div className="rounded-2xl border border-gray-100 bg-white p-8 text-center shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-100 text-2xl dark:bg-brand-900/40">
            🎁
          </div>
          <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
            Пробный период выдаётся в Telegram-боте
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Бесплатные {siteConfig.trialDays} дня полного доступа ко всем серверам — без привязки
            карты. Откройте бота и нажмите «Попробовать {siteConfig.trialDays} дня бесплатно»:
            ключи и ссылка для автонастройки придут сразу.
          </p>
          <div className="mt-6 flex flex-col gap-2">
            <BotTrialButton label={`Открыть бота и получить ${siteConfig.trialDays} дня бесплатно`} />
            <Link
              href="/#tariffs"
              className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
            >
              Или выберите тариф на сайте
            </Link>
          </div>
          <p className="mt-6 text-xs text-gray-400 dark:text-gray-500">
            Уже пользуетесь сервисом? Ключи и подписка — в личном кабинете.{" "}
            <Link href="/dashboard" className="text-brand-600 hover:underline dark:text-brand-400">
              В личный кабинет
            </Link>
          </p>
        </div>
      </div>
    </section>
  );
}

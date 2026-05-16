import { Metadata } from "next";
import Link from "next/link";
import { siteConfig } from "@/config/site";

export const metadata: Metadata = {
  title: "Ошибка оплаты",
};

export default function FailPage() {
  return (
    <section className="flex min-h-[60vh] items-center justify-center py-20">
      <div className="mx-auto max-w-md px-4 text-center">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-red-100 dark:bg-red-900/30">
          <svg
            className="h-8 w-8 text-red-600 dark:text-red-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </div>
        <h1 className="mt-6 text-2xl font-bold text-gray-900 dark:text-gray-100">
          Оплата не прошла
        </h1>
        <p className="mt-4 text-gray-600 dark:text-gray-400">
          К сожалению, платеж не был завершён. Пожалуйста, проверьте реквизиты
          и попробуйте снова.
        </p>
        <p className="mt-2 text-sm text-gray-400 dark:text-gray-500">
          Если проблема повторяется, обратитесь в поддержку:{" "}
          <a
            href={siteConfig.supportTelegram}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-600 underline dark:text-brand-400"
          >
            {siteConfig.supportTelegramHandle}
          </a>
        </p>
        <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            href="/#tariffs"
            className="rounded-xl bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            Попробовать снова
          </Link>
          <a
            href={siteConfig.supportTelegram}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-xl border border-gray-200 px-6 py-3 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Написать в поддержку
          </a>
        </div>
      </div>
    </section>
  );
}

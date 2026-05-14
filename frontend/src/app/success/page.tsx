import { Metadata } from "next";
import Link from "next/link";
import { siteConfig } from "@/config/site";

export const metadata: Metadata = {
  title: "Оплата прошла успешно",
};

export default function SuccessPage() {
  return (
    <section className="flex min-h-[60vh] items-center justify-center py-20">
      <div className="mx-auto max-w-md px-4 text-center">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-green-100">
          <svg
            className="h-8 w-8 text-green-600"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M5 13l4 4L19 7"
            />
          </svg>
        </div>
        <h1 className="mt-6 text-2xl font-bold text-gray-900">
          Оплата прошла успешно!
        </h1>
        <p className="mt-4 text-gray-600">
          Благодарим за оплату. Настройки защищённого сетевого доступа будут
          отправлены вам в Telegram-боте в ближайшее время.
        </p>
        <p className="mt-2 text-sm text-gray-400">
          Если вы не получили настройки в течение 10 минут, напишите в поддержку:{" "}
          <a
            href={siteConfig.supportTelegram}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-600 underline"
          >
            {siteConfig.supportTelegramHandle}
          </a>
        </p>
        <Link
          href="/"
          className="mt-8 inline-block rounded-xl bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
        >
          На главную
        </Link>
      </div>
    </section>
  );
}

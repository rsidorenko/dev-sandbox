import { siteConfig } from "@/config/site";
import Link from "next/link";

type Tariff = (typeof siteConfig.tariffs)[number];

type Props = {
  tariffs: readonly Tariff[];
};

export function TariffsSection({ tariffs }: Props) {
  return (
    <section id="tariffs" className="bg-gray-50 py-20">
      <div className="mx-auto max-w-6xl px-4">
        <div className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900">
            Тарифы
          </h2>
          <p className="mt-3 text-gray-500">
            Выберите подходящий период подписки
          </p>
        </div>

        <div className="mt-14 grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
          {tariffs.map((t) => (
            <div
              key={t.id}
              className={`relative rounded-2xl bg-white p-8 shadow-sm transition hover:shadow-md ${
                t.popular
                  ? "ring-2 ring-brand-500"
                  : "border border-gray-100"
              }`}
            >
              {t.popular && (
                <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-brand-600 px-4 py-1 text-xs font-bold text-white">
                  Популярный
                </span>
              )}
              <h3 className="text-lg font-semibold text-gray-900">
                {t.label}
              </h3>
              <div className="mt-4">
                <span className="text-4xl font-extrabold text-gray-900">
                  {t.priceLabel}
                </span>
                <span className="ml-2 text-sm text-gray-400">{t.perDay}</span>
              </div>
              <ul className="mt-6 flex flex-col gap-2 text-sm text-gray-600">
                <li className="flex items-center gap-2">
                  <Check /> Шифрование соединения
                </li>
                <li className="flex items-center gap-2">
                  <Check /> Персональный туннель
                </li>
                <li className="flex items-center gap-2">
                  <Check /> Все устройства
                </li>
                <li className="flex items-center gap-2">
                  <Check /> Техническая поддержка в Telegram
                </li>
              </ul>
              <Link
                href={`/payment/${t.id}`}
                className={`mt-8 block w-full rounded-xl py-3 text-center text-sm font-bold transition ${
                  t.popular
                    ? "bg-brand-600 text-white hover:bg-brand-700"
                    : "bg-gray-900 text-white hover:bg-gray-800"
                }`}
              >
                Оформить подписку
              </Link>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Check() {
  return (
    <svg
      className="h-4 w-4 shrink-0 text-brand-500"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2.5}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
    </svg>
  );
}

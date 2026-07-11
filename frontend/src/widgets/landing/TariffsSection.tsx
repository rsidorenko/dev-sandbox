"use client";

import { useState } from "react";
import Link from "next/link";
import { siteConfig } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";

type Tariff = (typeof siteConfig.tariffs)[number];

const CUSTOM_PRICE_PER_DAY = siteConfig.customPricePerDay;
const DEFAULT_DEVICES = 5;
const MIN_CUSTOM_DAYS = 1;
const MAX_CUSTOM_DAYS = 365;
const TRIAL_URL = `https://t.me/${siteConfig.botUsername}?start=trial`;

// Эталонный тариф для расчёта «выгоды» — самый дорогой в пересчёте на день (1 день).
const DAY1_PRICE = siteConfig.tariffs[0]?.price ?? 12;

function savingsPct(t: Tariff): number {
  const perDay = t.price / t.durationDays;
  const pct = Math.round((1 - perDay / DAY1_PRICE) * 100);
  return pct >= 10 ? pct : 0;
}

export function TariffsSection() {
  const tariffs = siteConfig.tariffs;
  const [selected, setSelected] = useState<string>(tariffs.find((t) => t.popular)?.id ?? tariffs[0].id);
  const [showCustom, setShowCustom] = useState(false);
  const [customDays, setCustomDays] = useState(30);

  const activeTariff = tariffs.find((t) => t.id === selected);

  return (
    <section id="tariffs" className="bg-gray-50 py-20 dark:bg-zinc-800/50">
      <div className="mx-auto max-w-4xl px-4">
        <div className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
            Тарифы
          </h2>
          <p className="mt-3 text-gray-500 dark:text-gray-400">
            Выберите подходящий период подписки
          </p>
        </div>

        {/* Tab pills */}
        <div className="mt-10 flex justify-center">
          <div className="inline-flex gap-2 overflow-x-auto rounded-2xl bg-white p-2 shadow-sm dark:bg-zinc-800 dark:shadow-none">
            {tariffs.map((t) => {
              const sp = savingsPct(t);
              const isBest = t.id === "365d";
              return (
                <button
                  key={t.id}
                  onClick={() => {
                    setSelected(t.id);
                    setShowCustom(false);
                  }}
                  className={`relative whitespace-nowrap rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                    selected === t.id && !showCustom
                      ? "bg-brand-600 text-white shadow-sm"
                      : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-zinc-700"
                  }`}
                >
                  {t.label}
                  {isBest && (
                    <span className="absolute -top-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-emerald-500 px-1.5 py-0.5 text-[10px] font-bold text-white">
                      Лучшая цена
                    </span>
                  )}
                  {!isBest && sp > 0 && (
                    <span className="absolute -top-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                      −{sp}%
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Detail card */}
        {showCustom ? (
          <CustomCard days={customDays} onDaysChange={setCustomDays} />
        ) : activeTariff ? (
          <TariffCard tariff={activeTariff} />
        ) : null}

        {/* Custom tariff toggle */}
        <div className="mt-8 flex justify-center">
          <button
            onClick={() => setShowCustom(!showCustom)}
            className={`inline-flex items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-3 text-sm font-semibold transition ${
              showCustom
                ? "border-brand-300 bg-brand-50 text-brand-700 dark:border-brand-600 dark:bg-brand-900/20 dark:text-brand-300"
                : "border-gray-300 bg-white text-gray-700 hover:border-brand-400 hover:bg-brand-50 hover:text-brand-700 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-300 dark:hover:border-brand-500 dark:hover:text-brand-300"
            }`}
          >
            {showCustom ? "↩ Назад к тарифам" : `📦 Свой тариф — от ${CUSTOM_PRICE_PER_DAY} ₽/день`}
          </button>
        </div>
      </div>
    </section>
  );
}

function UpsellHint({ currentId }: { currentId: string }) {
  // Апселл к более длинному тарифу — рост среднего чека. Не показываем для длинных.
  if (["3m", "6m", "365d"].includes(currentId)) return null;
  const target = siteConfig.tariffs.find((t) => t.id === "3m");
  if (!target) return null;
  return (
    <Link
      href={`/payment/${target.id}`}
      className="mt-3 block rounded-lg bg-emerald-50 px-3 py-2 text-center text-xs font-medium text-emerald-700 transition hover:bg-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-300 dark:hover:bg-emerald-900/40"
    >
      💡 Выгоднее: {target.label.toLowerCase()} за {target.price} ₽
    </Link>
  );
}

function TariffCard({ tariff }: { tariff: Tariff }) {
  const sp = savingsPct(tariff);
  const isBest = tariff.id === "365d";
  return (
    <div className="mx-auto mt-8 max-w-md">
      <div className="relative rounded-2xl bg-white p-8 shadow-sm dark:bg-zinc-800 dark:shadow-none">
        {(tariff.popular || isBest) && (
          <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-brand-600 px-4 py-1 text-xs font-bold text-white">
            {isBest ? "Лучшая цена" : "Популярный"}
          </span>
        )}

        <h3 className="text-center text-lg font-semibold text-gray-900 dark:text-gray-100">
          {tariff.label}
        </h3>

        <div className="mt-4 text-center">
          <span className="text-4xl font-extrabold text-gray-900 dark:text-white">
            {tariff.priceLabel}
          </span>
          <span className="ml-2 text-sm text-gray-400">{tariff.perDay}</span>
        </div>
        {sp > 0 && (
          <p className="mt-1 text-center text-xs font-semibold text-emerald-600 dark:text-emerald-400">
            Выгода −{sp}% в пересчёте на день
          </p>
        )}

        <ul className="mt-6 flex flex-col gap-2 text-sm text-gray-600 dark:text-gray-300">
          <li className="flex items-center gap-2">
            <Check /> Шифрование соединения
          </li>
          <li className="flex items-center gap-2">
            <Check /> Доступ к сервисам без ограничений
          </li>
          <li className="flex items-center gap-2">
            <Check /> До {DEFAULT_DEVICES} устройств
          </li>
          <li className="flex items-center gap-2">
            <Check /> Техническая поддержка в Telegram
          </li>
        </ul>

        <Link
          href={`/payment/${tariff.id}`}
          onClick={() => reachGoal("start_checkout", { plan: tariff.id })}
          className={`mt-8 block w-full rounded-xl py-3 text-center text-sm font-bold transition ${
            tariff.popular || isBest
              ? "bg-brand-600 text-white hover:bg-brand-700"
              : "bg-gray-900 text-white hover:bg-gray-800 dark:bg-zinc-200 dark:text-zinc-900 dark:hover:bg-zinc-100"
          }`}
        >
          Оформить подписку
        </Link>

        <div className="mt-3 text-center text-xs text-gray-400">
          🛡 Гарантия возврата {siteConfig.refundDays} дней
        </div>
        <div className="mt-1 text-center text-xs">
          <a href={TRIAL_URL} target="_blank" rel="noopener noreferrer" className="text-brand-600 hover:underline dark:text-brand-400">
            или попробуйте {siteConfig.trialDays} дня бесплатно
          </a>
        </div>

        <UpsellHint currentId={tariff.id} />
      </div>
    </div>
  );
}

function CustomCard({
  days,
  onDaysChange,
}: {
  days: number;
  onDaysChange: (d: number) => void;
}) {
  const price = days * CUSTOM_PRICE_PER_DAY;
  const clamped = Math.max(MIN_CUSTOM_DAYS, Math.min(MAX_CUSTOM_DAYS, days));

  return (
    <div className="mx-auto mt-8 max-w-md">
      <div className="rounded-2xl bg-white p-8 shadow-sm dark:bg-zinc-800 dark:shadow-none">
        <h3 className="text-center text-lg font-semibold text-gray-900 dark:text-gray-100">
          Свой тариф
        </h3>

        <div className="mt-6">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            Количество дней: <strong>{clamped}</strong>
          </label>
          <input
            type="range"
            min={MIN_CUSTOM_DAYS}
            max={MAX_CUSTOM_DAYS}
            value={clamped}
            onChange={(e) => onDaysChange(parseInt(e.target.value))}
            className="mt-2 w-full accent-brand-600"
          />
          <div className="flex justify-between text-xs text-gray-400">
            <span>1</span>
            <span>90</span>
            <span>180</span>
            <span>365</span>
          </div>
        </div>

        <div className="mt-4 text-center">
          <span className="text-4xl font-extrabold text-gray-900 dark:text-white">
            {price.toLocaleString("ru-RU")} ₽
          </span>
          <span className="ml-2 text-sm text-gray-400">
            ≈ {Math.round(price / clamped)} ₽/день
          </span>
        </div>

        <p className="mt-3 text-center text-sm text-gray-400">
          {CUSTOM_PRICE_PER_DAY} ₽ за каждый день
        </p>

        <Link
          href={`/payment/custom:${clamped}`}
          onClick={() => reachGoal("start_checkout", { plan: "custom" })}
          className="mt-6 block w-full rounded-xl bg-gray-900 py-3 text-center text-sm font-bold text-white transition hover:bg-gray-800 dark:bg-zinc-200 dark:text-zinc-900 dark:hover:bg-zinc-100"
        >
          Оформить подписку
        </Link>

        <div className="mt-3 text-center text-xs text-gray-400">
          🛡 Гарантия возврата {siteConfig.refundDays} дней
        </div>
      </div>
    </div>
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

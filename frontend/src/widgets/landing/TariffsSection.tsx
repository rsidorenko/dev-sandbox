"use client";

import { useState } from "react";
import Link from "next/link";
import { botTrialUrl, siteConfig } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";
import { savingsPctForPlan } from "@/entities/tariff/savings";
import { Reveal } from "@/shared/ui/Reveal";
import { usePromocode } from "@/features/promocode/usePromocode";
import { usePlans } from "@/features/plans/PlansProvider";
import { effectivePlanPrice } from "@/features/plans/effectivePrice";
import type { AppliedPromo } from "@/features/promocode/api";

type Tariff = (typeof siteConfig.tariffs)[number];

const CUSTOM_PRICE_PER_DAY = siteConfig.customPricePerDay;
const DEFAULT_DEVICES = 5;
const MIN_CUSTOM_DAYS = 1;
const MAX_CUSTOM_DAYS = 365;
const PRIMARY_TARIFF_IDS = new Set(["1m", "3m", "365d"]);

export function TariffsSection() {
  const tariffs = siteConfig.tariffs;
  const [selected, setSelected] = useState<string>(tariffs.find((t) => t.popular)?.id ?? tariffs[0].id);
  const [showMore, setShowMore] = useState(false);
  const [showCustom, setShowCustom] = useState(false);
  const [customDays, setCustomDays] = useState(30);
  const { applied } = usePromocode();
  // Runtime-цены (админ-оверрайды) + глобальная скидка; пока не загружены — siteConfig.
  const { prices, globalDiscount } = usePlans();

  const priceFor = (t: Tariff): number => prices[t.id] ?? t.price;
  const activeTariff = tariffs.find((t) => t.id === selected);
  const primaryTariffs = tariffs.filter((t) => PRIMARY_TARIFF_IDS.has(t.id));
  const otherTariffs = tariffs.filter((t) => !PRIMARY_TARIFF_IDS.has(t.id));

  return (
    <section id="tariffs" className="bg-gray-50 py-20 dark:bg-zinc-800/50">
      <div className="mx-auto max-w-4xl px-4">
        <Reveal className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
            Тарифы
          </h2>
          <p className="mt-3 text-gray-500 dark:text-gray-400">
            Выберите подходящий период подписки
          </p>
        </Reveal>

        {/* Tab pills */}
        <Reveal className="mt-10 flex justify-center">
          <div className="inline-flex max-w-full flex-wrap justify-center gap-2 rounded-2xl bg-white p-2 pt-4 shadow-sm dark:bg-zinc-800 dark:shadow-none">
            {primaryTariffs.map((t) => {
              const sp = savingsPctForPlan(t.id);
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
                    <span className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-emerald-500 px-1.5 py-0.5 text-[10px] font-bold text-white">
                      Лучшая цена
                    </span>
                  )}
                  {!isBest && sp > 0 && (
                    <span className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                      −{sp}%
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </Reveal>

        {showMore && (
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {otherTariffs.map((t) => (
              <button
                key={t.id}
                onClick={() => {
                  setSelected(t.id);
                  setShowCustom(false);
                }}
                className={`whitespace-nowrap rounded-xl px-4 py-2 text-sm font-semibold transition ${
                  selected === t.id && !showCustom
                    ? "bg-brand-600 text-white shadow-sm"
                    : "border border-gray-200 bg-white text-gray-600 hover:border-brand-300 hover:text-brand-700 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-300"
                }`}
              >
                {t.label}
              </button>
            ))}
            <button
              onClick={() => setShowCustom(true)}
              className={`whitespace-nowrap rounded-xl px-4 py-2 text-sm font-semibold transition ${
                showCustom
                  ? "bg-brand-600 text-white shadow-sm"
                  : "border border-gray-200 bg-white text-gray-600 hover:border-brand-300 hover:text-brand-700 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-300"
              }`}
            >
              Свой срок
            </button>
          </div>
        )}

        {/* Detail card */}
        <div key={showCustom ? "custom" : selected} className="animate-fade-in-up">
          {showCustom ? (
            <CustomCard days={customDays} onDaysChange={setCustomDays} />
          ) : activeTariff ? (
            <TariffCard
              tariff={activeTariff}
              applied={applied}
              globalDiscount={globalDiscount}
              runtimePrice={priceFor(activeTariff)}
              upsellPrice={priceFor(siteConfig.tariffs.find((t) => t.id === "3m") ?? activeTariff)}
            />
          ) : null}
        </div>

        {/* Второстепенные варианты не конкурируют с тремя основными тарифами. */}
        <div className="mt-8 flex justify-center">
          <button
            onClick={() => {
              const next = !showMore;
              setShowMore(next);
              if (!next) {
                setShowCustom(false);
                if (!PRIMARY_TARIFF_IDS.has(selected)) setSelected("3m");
              }
            }}
            className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-brand-700 transition hover:bg-brand-50 dark:text-brand-300 dark:hover:bg-brand-900/20"
          >
            {showMore
              ? "Скрыть другие варианты"
              : `Другие сроки и свой тариф от ${CUSTOM_PRICE_PER_DAY} ₽/день`}
          </button>
        </div>
      </div>
    </section>
  );
}

function UpsellHint({ currentId, targetPrice }: { currentId: string; targetPrice: number | null }) {
  // Апселл к более длинному тарифу — рост среднего чека. Не показываем для длинных.
  if (["3m", "6m", "365d"].includes(currentId)) return null;
  const target = siteConfig.tariffs.find((t) => t.id === "3m");
  if (!target || targetPrice == null) return null;
  return (
    <Link
      href={`/payment/${target.id}`}
      className="mt-3 block rounded-lg bg-emerald-50 px-3 py-2 text-center text-xs font-medium text-emerald-700 transition hover:bg-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-300 dark:hover:bg-emerald-900/40"
    >
      💡 Выгоднее: {target.label.toLowerCase()} за {targetPrice.toLocaleString("ru-RU")} ₽
    </Link>
  );
}

function TariffCard({
  tariff,
  applied,
  globalDiscount,
  runtimePrice,
  upsellPrice,
}: {
  tariff: Tariff;
  applied: AppliedPromo | null;
  globalDiscount: ReturnType<typeof usePlans>["globalDiscount"];
  runtimePrice: number;
  upsellPrice: number | null;
}) {
  const sp = savingsPctForPlan(tariff.id);
  const isBest = tariff.id === "365d";
  // База — runtime-цена (админ-оверрайд или дефолт); итог — best-of(промо, акция).
  const eff = effectivePlanPrice({
    base: runtimePrice,
    planId: tariff.id,
    applied,
    globalDiscount,
  });
  const hasDiscount = eff.discounted < runtimePrice;
  const perDay = `≈ ${Math.max(1, Math.round(runtimePrice / tariff.durationDays))} ₽/день`;
  return (
    <div className="mx-auto mt-8 max-w-md">
      <div className="relative rounded-2xl bg-white p-8 shadow-sm dark:bg-zinc-800 dark:shadow-none">
        {(tariff.popular || isBest) && (
          <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-brand-600 px-4 py-1 text-xs font-bold text-white">
            {isBest ? "Лучшая цена" : "Рекомендуем"}
          </span>
        )}

        <h3 className="text-center text-lg font-semibold text-gray-900 dark:text-gray-100">
          {tariff.label}
        </h3>

        <div className="mt-4 text-center">
          {hasDiscount ? (
            <div className="flex items-baseline justify-center gap-2">
              <span className="text-2xl font-bold text-gray-400 line-through dark:text-gray-500">
                {runtimePrice.toLocaleString("ru-RU")} ₽
              </span>
              <span className="text-4xl font-extrabold text-brand-600 dark:text-brand-400">
                {eff.discounted.toLocaleString("ru-RU")} ₽
              </span>
            </div>
          ) : (
            <span className="text-4xl font-extrabold text-gray-900 dark:text-white">
              {runtimePrice.toLocaleString("ru-RU")} ₽
            </span>
          )}
          <span className="ml-2 text-sm text-gray-400">{perDay}</span>
        </div>
        {hasDiscount && eff.winner === "promo" && applied ? (
          <p className="mt-1 text-center text-xs font-semibold text-emerald-600 dark:text-emerald-400">
            🎁 Промокод {applied.code}: скидка {runtimePrice - eff.discounted} ₽
          </p>
        ) : hasDiscount && eff.winner === "global" ? (
          <p className="mt-1 text-center text-xs font-semibold text-emerald-600 dark:text-emerald-400">
            🎁 {globalDiscount?.public_label || "Акция"} {globalDiscount?.label}: скидка{" "}
            {runtimePrice - eff.discounted} ₽
          </p>
        ) : sp > 0 ? (
          <p className="mt-1 text-center text-xs font-semibold text-emerald-600 dark:text-emerald-400">
            Выгода −{sp}% в пересчёте на день
          </p>
        ) : null}

        <ul className="mt-6 flex flex-col gap-2 text-sm text-gray-600 dark:text-gray-300">
          <li className="flex items-center gap-2">
            <Check /> Шифрование соединения
          </li>
          <li className="flex items-center gap-2">
            <Check /> Защита трафика в публичных сетях
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
          <a
            href={botTrialUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-600 hover:underline dark:text-brand-400"
          >
            или попробуйте {siteConfig.trialDays} дня бесплатно
          </a>
        </div>

        <UpsellHint currentId={tariff.id} targetPrice={upsellPrice} />
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

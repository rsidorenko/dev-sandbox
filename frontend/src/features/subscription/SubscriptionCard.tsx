"use client";

import { useState } from "react";
import Link from "next/link";
import { subscriptionApi } from "@/entities/subscription/api";
import { siteConfig } from "@/shared/config/site";
import { useCopyToClipboard } from "@/shared/hooks/useCopyToClipboard";
import {
  PLANS,
  planName,
  normalizePlanId,
  daysLeft,
} from "@/entities/tariff/helpers";

type Subscription = {
  state: string;
  active_until: string | null;
  plan_id: string | null;
  device_count: number | null;
};

type Referral = {
  code: string;
  balance_rubles: number;
  referrals_count: number;
  web_referral_link?: string;
};

type Props = {
  sub: Subscription | null;
  referral: Referral | null;
  onRefresh: () => Promise<void>;
  onOpenKeys: () => void;
  onRenew: () => void;
};

export function SubscriptionCard({
  sub,
  referral,
  onRefresh,
  onOpenKeys,
  onRenew,
}: Props) {
  const isActive = sub?.state === "active";
  const days = sub?.active_until ? daysLeft(sub.active_until) : 0;

  const [showChangePlan, setShowChangePlan] = useState(false);
  const [showChangeDevices, setShowChangeDevices] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState(
    sub?.plan_id || "plan_1m",
  );
  const [selectedDevices, setSelectedDevices] = useState(
    Math.max(5, sub?.device_count || 5),
  );
  const [actionLoading, setActionLoading] = useState(false);
  const { copy, isCopied } = useCopyToClipboard();

  const handleChangePlan = () => {
    window.location.href = `/payment/${normalizePlanId(selectedPlan)}`;
  };

  const handleChangeDevices = () => {
    window.location.href = `/payment/${normalizePlanId(sub?.plan_id)}?devices=${selectedDevices}`;
  };

  const handleCancel = async () => {
    setActionLoading(true);
    await subscriptionApi.cancel();
    await onRefresh();
    setActionLoading(false);
    setShowCancelConfirm(false);
  };

  return (
    <>
      {/* Subscription */}
      <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
          Подписка
        </h2>
        {sub && isActive ? (
          <div className="mt-4 space-y-4">
            <div className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-green-500 shadow-sm shadow-green-500/50" />
              <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                Активна
              </span>
              {sub.active_until && (
                <span className="ml-auto text-xs text-gray-400">
                  до{" "}
                  {new Date(sub.active_until).toLocaleDateString("ru-RU")}
                </span>
              )}
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-xl bg-gradient-to-br from-brand-50 to-brand-100/50 p-3 text-center dark:from-brand-950/30 dark:to-brand-900/20">
                <p className="text-xs text-brand-600 dark:text-brand-400">
                  Тариф
                </p>
                <p className="mt-1 text-sm font-bold text-brand-800 dark:text-brand-300">
                  {planName(sub.plan_id)}
                </p>
              </div>
              <div className="rounded-xl bg-gradient-to-br from-green-50 to-emerald-100/50 p-3 text-center dark:from-green-950/30 dark:to-emerald-900/20">
                <p className="text-xs text-green-600 dark:text-green-400">
                  Осталось
                </p>
                <p className="mt-1 text-sm font-bold text-green-800 dark:text-green-300">
                  {days} дн.
                </p>
              </div>
              <div className="rounded-xl bg-gradient-to-br from-violet-50 to-purple-100/50 p-3 text-center dark:from-violet-950/30 dark:to-purple-900/20">
                <p className="text-xs text-violet-600 dark:text-violet-400">
                  Устройств
                </p>
                <p className="mt-1 text-sm font-bold text-violet-800 dark:text-violet-300">
                  {sub.device_count || "—"}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={onOpenKeys}
                className="group flex items-center gap-3 rounded-xl bg-brand-600 px-4 py-3 text-left transition hover:bg-brand-700"
              >
                <svg
                  className="h-5 w-5 text-brand-200 transition group-hover:text-white"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"
                  />
                </svg>
                <div>
                  <p className="text-sm font-semibold text-white">
                    Показать ключи
                  </p>
                  <p className="text-xs text-brand-200">Настройки и ссылка</p>
                </div>
              </button>

              <button
                onClick={onRenew}
                className="group flex items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-3 text-left transition hover:bg-brand-100 dark:border-brand-800 dark:bg-brand-950/30 dark:hover:bg-brand-900/40"
              >
                <svg
                  className="h-5 w-5 text-brand-500 dark:text-brand-400"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182"
                  />
                </svg>
                <div>
                  <p className="text-sm font-semibold text-brand-700 dark:text-brand-300">
                    Продлить
                  </p>
                  <p className="text-xs text-brand-500 dark:text-brand-500">
                    Перейти к оплате
                  </p>
                </div>
              </button>

              <button
                onClick={() => {
                  setShowChangePlan(true);
                  setShowChangeDevices(false);
                }}
                className="group flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800/50 dark:hover:bg-zinc-700/50"
              >
                <svg
                  className="h-5 w-5 text-gray-400 dark:text-zinc-400"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75"
                  />
                </svg>
                <div>
                  <p className="text-sm font-semibold text-gray-700 dark:text-zinc-200">
                    Сменить тариф
                  </p>
                  <p className="text-xs text-gray-400 dark:text-zinc-500">
                    1 / 3 / 6 месяцев
                  </p>
                </div>
              </button>

              <button
                onClick={() => {
                  setShowChangeDevices(true);
                  setShowChangePlan(false);
                }}
                className="group flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800/50 dark:hover:bg-zinc-700/50"
              >
                <svg
                  className="h-5 w-5 text-gray-400 dark:text-zinc-400"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M10.5 1.5H8.25A2.25 2.25 0 006 3.75v16.5a2.25 2.25 0 002.25 2.25h7.5A2.25 2.25 0 0018 20.25V3.75a2.25 2.25 0 00-2.25-2.25H13.5m-3 0V3h3V1.5m-3 0h3m-3 18.75h3"
                  />
                </svg>
                <div>
                  <p className="text-sm font-semibold text-gray-700 dark:text-zinc-200">
                    Устройства
                  </p>
                  <p className="text-xs text-gray-400 dark:text-zinc-500">
                    {sub.device_count || 5} шт.
                  </p>
                </div>
              </button>
            </div>

            <Link
              href="/instructions"
              className="flex items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-3 text-center text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800/50 dark:text-zinc-200 dark:hover:bg-zinc-700/50"
            >
              Инструкция по подключению
            </Link>

            <div className="text-center">
              <button
                onClick={() => setShowCancelConfirm(true)}
                className="text-xs text-gray-400 transition hover:text-red-500 dark:text-zinc-500 dark:hover:text-red-400"
              >
                Отменить подписку
              </button>
            </div>

            {showChangePlan && (
              <div className="rounded-xl border border-gray-200 p-4 dark:border-zinc-600">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">
                  Выберите тариф:
                </p>
                <div className="flex flex-wrap gap-2">
                  {PLANS.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => setSelectedPlan(p.id)}
                      className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                        selectedPlan === p.id
                          ? "bg-brand-600 text-white shadow-sm"
                          : "border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleChangePlan}
                    className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
                  >
                    Перейти к оплате
                  </button>
                  <button
                    onClick={() => setShowChangePlan(false)}
                    className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                  >
                    Отмена
                  </button>
                </div>
              </div>
            )}

            {showChangeDevices && (
              <div className="rounded-xl border border-gray-200 p-4 dark:border-zinc-600">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">
                  Количество устройств:{" "}
                  <strong className="text-brand-600 dark:text-brand-400">
                    {selectedDevices}
                  </strong>
                </p>
                <input
                  type="range"
                  min={5}
                  max={20}
                  value={selectedDevices}
                  onChange={(e) => setSelectedDevices(Number(e.target.value))}
                  className="w-full accent-brand-600"
                />
                <div className="flex justify-between text-xs text-gray-400 mt-1">
                  <span>5</span>
                  <span>10</span>
                  <span>15</span>
                  <span>20</span>
                </div>
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleChangeDevices}
                    className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
                  >
                    Перейти к оплате
                  </button>
                  <button
                    onClick={() => setShowChangeDevices(false)}
                    className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                  >
                    Отмена
                  </button>
                </div>
              </div>
            )}

            {showCancelConfirm && (
              <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-800/50 dark:bg-red-950/20">
                <p className="text-sm text-red-600 dark:text-red-400 mb-3">
                  Подписка будет отменена. Доступ сохранится до конца
                  оплаченного периода.
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={handleCancel}
                    disabled={actionLoading}
                    className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    {actionLoading ? "Отмена..." : "Да, отменить"}
                  </button>
                  <button
                    onClick={() => setShowCancelConfirm(false)}
                    className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                  >
                    Назад
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="mt-3">
            <p className="text-sm text-gray-600 dark:text-gray-300">
              {sub
                ? "Подписка отменена или истекла."
                : "У вас нет активной подписки."}
            </p>
            <div className="mt-3">
              <Link
                href="/#tariffs"
                className="inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700"
              >
                Оформить подписку
              </Link>
            </div>
          </div>
        )}
      </div>

      {/* Referral */}
      {referral && (
        <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
            Реферальная программа
          </h2>
          <div className="mt-3 space-y-2">
            <p className="text-sm text-gray-600 dark:text-gray-300">
              Баланс:{" "}
              <strong>{referral.balance_rubles.toFixed(2)} ₽</strong>
            </p>
            <p className="text-sm text-gray-600 dark:text-gray-300">
              Рефералов: <strong>{referral.referrals_count}</strong>
            </p>
            <div className="mt-3 rounded-lg bg-gradient-to-br from-amber-50 to-orange-100/50 p-4 dark:from-amber-950/30 dark:to-orange-900/20">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
                Отправьте ссылку другу — получите процент с его оплаты
              </p>
              {/* Telegram link */}
              <div className="mt-3">
                <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                  🔗 Для Telegram <span className="font-normal text-gray-400">— друг без сайта</span>
                </p>
                <p className="mt-1 truncate text-xs font-mono text-gray-600 dark:text-gray-400">
                  https://t.me/{siteConfig.botUsername}?start=ref_{referral.code}
                </p>
                <button
                  onClick={() =>
                    copy(
                      `https://t.me/${siteConfig.botUsername}?start=ref_${referral.code}`,
                      "ref-tg",
                    )
                  }
                  className="mt-1.5 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700"
                >
                  {isCopied("ref-tg") ? "✓ Скопировано!" : "Копировать"}
                </button>
              </div>
              {/* Web link */}
              {referral.web_referral_link && (
                <div className="mt-3 border-t border-amber-200/50 pt-3 dark:border-amber-800/30">
                  <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                    🌐 Для сайта <span className="font-normal text-gray-400">— друг без Telegram</span>
                  </p>
                  <p className="mt-1 truncate text-xs font-mono text-gray-600 dark:text-gray-400">
                    {referral.web_referral_link}
                  </p>
                  <button
                    onClick={() => copy(referral.web_referral_link!, "ref-web")}
                    className="mt-1.5 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700"
                  >
                    {isCopied("ref-web") ? "✓ Скопировано!" : "Копировать"}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

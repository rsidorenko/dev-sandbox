"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CatalogUnavailable, usePlans } from "@/features/plans/PlansProvider";
import { subscriptionApi } from "@/entities/subscription/api";
import { paymentApi } from "@/entities/payment/api";
import { calcExtraDeviceCost, getTariffInfo } from "@/entities/payment/calc";
import {
  PLANS,
  planName,
  normalizePlanId,
  daysLeft,
} from "@/entities/tariff/helpers";
import {
  savingsPctForPlan,
  perDayRub,
  priceForPlan,
} from "@/entities/tariff/savings";

type Subscription = {
  state: string;
  active_until: string | null;
  remaining_days: number | null;
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
  const router = useRouter();
  const { catalog } = usePlans();
  const isActive = sub?.state === "active";
  // Prefer the server-computed remaining_days (shared with the bot -> identical
  // 'days left' on web and Telegram). Fall back to local daysLeft for safety.
  const days = sub?.remaining_days ?? (sub?.active_until ? daysLeft(sub.active_until) : 0);

  const [showChangePlan, setShowChangePlan] = useState(false);
  const [showChangeDevices, setShowChangeDevices] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState(
    sub?.plan_id || "plan_1m",
  );
  // Device top-up can only ADD devices (never remove paid-for ones), so the
  // selectable floor is the current device count (min 5).
  const deviceMin = Math.max(5, sub?.device_count || 5);
  const [selectedDevices, setSelectedDevices] = useState(deviceMin);
  const [offerAccepted, setOfferAccepted] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [deviceError, setDeviceError] = useState("");
  const [deviceNotice, setDeviceNotice] = useState("");
  // One idempotency key per mount for the add-device-via-balance button.
  // Lazy-init so crypto.randomUUID() runs ONCE, not on every render.
  const addDeviceIdempotencyRef = useRef<string | null>(null);
  if (addDeviceIdempotencyRef.current === null) {
    addDeviceIdempotencyRef.current =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  const addDeviceIdempotencyKey = addDeviceIdempotencyRef.current;
  const tariffInfo = sub?.plan_id ? getTariffInfo(normalizePlanId(sub.plan_id), catalog) : null;
  const addDeviceExtra = Math.max(0, selectedDevices - deviceMin);
  const addDeviceCost = tariffInfo ? calcExtraDeviceCost(tariffInfo.durationDays, addDeviceExtra, catalog!.extra_device_price_rubles) : 0;
  const balanceRubles = referral?.balance_rubles ?? 0;
  const canAddWithBalance = referral != null && addDeviceCost > 0 && balanceRubles >= addDeviceCost;

  const handleChangePlan = () => {
    // Клиентская навигация: полная перезагрузка теряет токен сессии в памяти.
    router.push(`/payment/${normalizePlanId(selectedPlan)}`);
  };

  const handleChangeDevices = async () => {
    if (!offerAccepted || !catalog) return;
    setDeviceError("");
    setActionLoading(true);
    try {
      const res = await paymentApi.addDevices(selectedDevices, "card", offerAccepted);
      if (!res.ok) {
        // Map backend error codes to specific messages (parity with handleAddDevicesWithBalance).
        if (res.error === "no_active_subscription") {
          setDeviceError("Нет активной подписки.");
        } else if (res.error === "device_count_not_higher") {
          setDeviceError("Выберите больше устройств, чем у вас уже есть.");
        } else if (res.error === "unauthorized") {
          setDeviceError("Войдите, чтобы изменить количество устройств.");
        } else {
          setDeviceError("Не удалось создать платёж. Попробуйте позже.");
        }
        return;
      }
      if (res.data.payment_url) {
        window.location.href = res.data.payment_url;
      }
    } finally {
      setActionLoading(false);
    }
  };

  const handleAddDevicesWithBalance = async () => {
    if (!offerAccepted || !catalog) return;
    setDeviceError("");
    setActionLoading(true);
    try {
      const res = await paymentApi.addDevicesWithBalance(selectedDevices, addDeviceIdempotencyKey, addDeviceCost * 100, offerAccepted);
      if (!res.ok) {
        // Backend returns these as HTTP 4xx/5xx with {ok:false, error:<code>} — map each.
        if (res.error === "insufficient_balance") {
          setDeviceError("Недостаточно средств на балансе.");
          await onRefresh();
        } else if (res.error === "payment_in_progress") {
          setDeviceError("Платёж уже обрабатывается. Попробуйте обновить страницу через минуту.");
        } else if (res.error === "no_active_subscription") {
          setDeviceError("Нет активной подписки. Оформите подписку прежде чем добавлять устройства.");
        } else if (res.error === "device_count_not_higher") {
          setDeviceError("Выберите больше устройств, чем у вас уже есть.");
        } else {
          setDeviceError("Не удалось оплатить с баланса. Попробуйте позже.");
        }
        return;
      }
      setDeviceNotice(res.data.status === "provisioning"
        ? "Оплата принята. Лимит устройств обновится автоматически. Повторно платить не нужно."
        : "");
      await onRefresh();
      setShowChangeDevices(false);
    } finally {
      setActionLoading(false);
    }
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
      {deviceNotice && <p role="status" className="mt-4 text-sm text-brand-700 dark:text-brand-300">{deviceNotice}</p>}
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

            {showChangePlan && !catalog && <CatalogUnavailable />}
            {showChangePlan && catalog && (
              <div className="rounded-xl border border-gray-200 p-4 pt-5 dark:border-zinc-600">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">
                  Выберите тариф:
                </p>
                <div className="flex flex-wrap gap-x-3 gap-y-6 pt-3">
                  {PLANS.map((p) => {
                    const sp = savingsPctForPlan(p.id, catalog);
                    const isSelected = selectedPlan === p.id;
                    return (
                      <button
                        key={p.id}
                        onClick={() => setSelectedPlan(p.id)}
                        className={`relative rounded-lg px-4 py-2 text-sm font-medium transition ${
                          isSelected
                            ? "bg-brand-600 text-white shadow-sm"
                            : "border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                        }`}
                      >
                        {p.label}
                        {sp > 0 && (
                          <span
                            className={`absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full px-1.5 py-px text-[10px] font-bold leading-none ${
                              isSelected
                                ? "bg-white text-brand-700"
                                : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                            }`}
                          >
                            −{sp}%
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
                {/* Сводка по выбранному тарифу: цена, ₽/день, выгода */}
                <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
                  {planName(selectedPlan)} · {priceForPlan(selectedPlan, catalog)} ₽
                  <span className="text-gray-400"> · ≈ {perDayRub(selectedPlan, catalog)} ₽/день</span>
                  {savingsPctForPlan(selectedPlan, catalog) > 0 && (
                    <span className="ml-1 font-semibold text-emerald-600 dark:text-emerald-400">
                      · выгода −{savingsPctForPlan(selectedPlan, catalog)}%
                    </span>
                  )}
                </p>
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
                  min={deviceMin}
                  max={20}
                  value={selectedDevices}
                  onChange={(e) => {
                    setSelectedDevices(Number(e.target.value));
                    setDeviceError("");
                  }}
                  className="w-full accent-brand-600"
                />
                <div className="flex justify-between text-xs text-gray-400 mt-1">
                  <span>{deviceMin}</span>
                  <span>10</span>
                  <span>15</span>
                  <span>20</span>
                </div>
                <label className="mt-3 flex gap-2 text-sm"><input type="checkbox" checked={offerAccepted} onChange={e => setOfferAccepted(e.target.checked)} />
                  <span>Принимаю <Link href="/offer" target="_blank" className="underline">оферту</Link> и стоимость добавления устройств.</span>
                </label>
                <p className="mt-2 text-xs"><Link href="/privacy" target="_blank" className="underline">Политика обработки данных</Link></p>
                {deviceError && (
                  <p className="mt-2 text-sm text-red-500">{deviceError}</p>
                )}
                <div className="flex flex-wrap gap-2 mt-3">
                  {canAddWithBalance && (
                    <button
                      onClick={handleAddDevicesWithBalance}
                      disabled={actionLoading || !catalog || !offerAccepted}
                      className="rounded-lg border border-brand-200 bg-brand-50 px-4 py-2 text-sm font-semibold text-brand-700 hover:bg-brand-100 disabled:opacity-50 dark:border-brand-800 dark:bg-brand-900/20 dark:text-brand-300"
                    >
                      {actionLoading ? "Обработка..." : `С баланса ${addDeviceCost} ₽`}
                    </button>
                  )}
                  <button
                    onClick={handleChangeDevices}
                    disabled={actionLoading || !catalog || !offerAccepted}
                    className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
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
    </>
  );
}

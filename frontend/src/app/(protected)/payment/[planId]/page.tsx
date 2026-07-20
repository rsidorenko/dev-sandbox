"use client";

import { Suspense, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { paymentApi } from "@/entities/payment/api";
import { useAuth } from "@/shared/lib/auth";
import { siteConfig } from "@/shared/config/site";
import {
  calcExtraDeviceCost,
  getTariffInfo,
  DEFAULT_DEVICES,
  EXTRA_DEVICE_PRICE,
} from "@/entities/payment/calc";
import { reachGoal } from "@/shared/lib/analytics";
import { Alert } from "@/shared/ui/Alert";

export default function PaymentPage() {
  return (
    <Suspense
      fallback={
        <section className="flex min-h-[70vh] items-center justify-center">
          <p className="text-gray-500">Загрузка...</p>
        </section>
      }
    >
      <PaymentForm />
    </Suspense>
  );
}

function PaymentForm() {
  const { planId: rawPlanId } = useParams<{ planId: string }>();
  // Next.js отдаёт динамический сегмент URL-закодированным (напр. "custom%3A10"
  // для /payment/custom:10), поэтому декодируем — иначе расчёт тарифа и запрос
  // оплаты для «своего тарифа» (custom:N) не сработают.
  const planId = rawPlanId ? decodeURIComponent(rawPlanId) : "";
  const router = useRouter();
  const searchParams = useSearchParams();
  const refParam = searchParams.get("ref");
  const { loading: authLoading, authenticated, profile, refresh } = useAuth();
  const [deviceCount, setDeviceCount] = useState(() => {
    const d = searchParams.get("devices");
    return d ? Math.max(DEFAULT_DEVICES, parseInt(d) || DEFAULT_DEVICES) : DEFAULT_DEVICES;
  });
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [paymentResult, setPaymentResult] = useState<{
    status: string;
    amount_rubles: number;
    message: string;
  } | null>(null);
  // Balance-payment: one idempotency key per mount (survives re-render, dedups
  // double-clicks/retries server-side). Balance-loading + inline result state.
  const balanceIdempotencyKey = useRef(crypto.randomUUID()).current;
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balanceResult, setBalanceResult] = useState<{
    active_until?: string;
    balance_rubles_remaining?: number;
  } | null>(null);
  const [balanceError, setBalanceError] = useState("");
  const balanceRubles = profile?.referral?.balance_rubles ?? 0;
  const busy = loading || balanceLoading;

  const info = planId ? getTariffInfo(planId) : null;

  if (!info) {
    return (
      <section className="flex min-h-[60vh] items-center justify-center py-20">
        <div className="mx-auto max-w-md px-4 text-center">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Тариф не найден</h1>
          <Link
            href="/#tariffs"
            className="mt-6 inline-block rounded-xl bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            Выбрать тариф
          </Link>
        </div>
      </section>
    );
  }

  // Неавторизованный пользователь видит тариф и встроенный gate вместо жёсткого
  // редиректа на /login — оффер остаётся в контексте, есть быстрый путь через
  // Telegram-бота (бот уже знает пользователя, оплата в один клик).
  // Но если только что успешно оплачено с баланса — НЕ заменяем форму на gate,
  // иначе refresh() (который transiently сбрасывает auth-состояние) скроет success.
  if (!authLoading && !authenticated && planId && !balanceResult) {
    return <AuthGate info={info} planId={planId} refParam={refParam} />;
  }

  const extraDevices = Math.max(0, deviceCount - DEFAULT_DEVICES);
  const extraCost = calcExtraDeviceCost(info.durationDays, extraDevices);
  const total = info.basePrice + extraCost;
  const canPayWithBalance = profile?.referral != null && balanceRubles >= total;

  const handlePayWithBalance = async (e: FormEvent) => {
    e.preventDefault();
    setBalanceError("");
    setBalanceLoading(true);
    const result = await paymentApi.payWithBalance(planId, deviceCount, balanceIdempotencyKey);
    setBalanceLoading(false);
    if (!result.ok) {
      // Backend returns these as HTTP 4xx/5xx with {ok:false, error:<code>} (no `status`
      // field) — apiFetch surfaces them as result.error. Map each to a specific message.
      if (result.error === "insufficient_balance") {
        setBalanceError("Недостаточно средств на балансе. Приглашайте друзей по реферальной ссылке.");
        await refresh();
      } else if (result.error === "payment_in_progress") {
        setBalanceError("Платёж уже обрабатывается. Попробуйте обновить страницу через минуту.");
      } else if (result.error === "vless_creation_failed") {
        setBalanceError("Не удалось выпустить VPN-ключи — средства возвращены на баланс.");
        await refresh();
      } else {
        setBalanceError("Не удалось оплатить с баланса. Попробуйте позже.");
      }
      return;
    }
    // Only `completed` / `already_completed` arrive as {ok:true, data:{status:...}}.
    const data = result.data;
    setBalanceResult({
      active_until: data.active_until,
      balance_rubles_remaining: data.balance_rubles_remaining,
    });
    await refresh();
  };

  const handlePay = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg("");
    setLoading(true);

    const result = await paymentApi.create(planId, deviceCount);
    setLoading(false);

    if (!result.ok) {
      if (result.error === "unauthorized") {
        router.push(`/login?next=/payment/${planId}${refParam ? `&ref=${encodeURIComponent(refParam)}` : ""}`);
        return;
      }
      setErrorMsg("Не удалось создать платёж. Попробуйте позже.");
      return;
    }

    const data = result.data;
    if (data.payment_url) {
      window.location.href = data.payment_url;
      return;
    }

    setPaymentResult({
      status: data.status,
      amount_rubles: data.amount_rubles,
      message: data.message || "",
    });
  };

  return (
    <section className="py-12">
      <div className="mx-auto max-w-lg px-4">
        <Link
          href="/#tariffs"
          className="text-sm font-medium text-brand-600 transition hover:text-brand-700 dark:text-brand-400"
        >
          &larr; Назад к тарифам
        </Link>

        <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-8 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Оформление подписки</h1>

          <div className="mt-6 space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Тариф</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{info.label}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Период</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{info.durationDays} дней</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Подписка</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{info.basePrice.toLocaleString("ru-RU")} ₽</span>
            </div>
          </div>

          <div className="mt-6">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Количество устройств: <strong>{deviceCount}</strong>
            </label>
            <input
              type="range"
              min={5}
              max={20}
              value={deviceCount}
              onChange={(e) => setDeviceCount(parseInt(e.target.value))}
              className="mt-2 w-full accent-brand-600"
            />
            <div className="flex justify-between text-xs text-gray-400">
              <span>5</span><span>10</span><span>15</span><span>20</span>
            </div>
            {extraDevices > 0 && (
              <p className="mt-1 text-xs text-gray-400">
                Доп. устройства: {extraDevices} &times; {Math.round(EXTRA_DEVICE_PRICE / 30 * info.durationDays)} ₽ = {extraCost.toLocaleString("ru-RU")} ₽
              </p>
            )}
          </div>

          <div className="mt-6 flex items-center justify-between rounded-xl bg-gray-50 px-4 py-3 dark:bg-zinc-700/50">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Итого</span>
            <span className="text-xl font-bold text-gray-900 dark:text-gray-100">{total.toLocaleString("ru-RU")} ₽</span>
          </div>

          {errorMsg && <Alert className="mt-4">{errorMsg}</Alert>}

          {paymentResult && paymentResult.status === "payment_unavailable" && (
            <Alert variant="info" className="mt-4">
              <p className="text-sm text-brand-900 dark:text-brand-200">{paymentResult.message}</p>
              <p className="mt-2 text-sm font-medium text-brand-700 dark:text-brand-300">
                Сумма: {paymentResult.amount_rubles} ₽
              </p>
            </Alert>
          )}

          {balanceResult && (
            <Alert variant="info" className="mt-4">
              <p className="text-sm font-medium text-brand-900 dark:text-brand-200">
                ✅ Подписка оплачена с реферального баланса!
              </p>
              {balanceResult.active_until && (
                <p className="mt-1 text-sm text-brand-900 dark:text-brand-200">
                  Действует до: {balanceResult.active_until}
                </p>
              )}
              {typeof balanceResult.balance_rubles_remaining === "number" && (
                <p className="mt-1 text-xs text-brand-700 dark:text-brand-300">
                  Остаток на балансе: {balanceResult.balance_rubles_remaining.toLocaleString("ru-RU")} ₽
                </p>
              )}
              <Link
                href="/dashboard"
                className="mt-3 inline-block rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700"
              >
                Открыть ключи
              </Link>
            </Alert>
          )}

          {balanceError && <Alert className="mt-4">{balanceError}</Alert>}

          {canPayWithBalance && !balanceResult && (
            <button
              onClick={handlePayWithBalance}
              disabled={busy}
              className="mt-6 w-full rounded-xl border border-brand-200 bg-brand-50 py-3 text-sm font-bold text-brand-700 transition hover:bg-brand-100 disabled:opacity-50 dark:border-brand-800 dark:bg-brand-900/20 dark:text-brand-300"
            >
              {balanceLoading
                ? "Обработка..."
                : `Оплатить с баланса ${balanceRubles.toLocaleString("ru-RU", { minimumFractionDigits: 2 })} ₽`}
            </button>
          )}

          <button
            onClick={handlePay}
            disabled={busy || !!balanceResult}
            className="mt-3 w-full rounded-xl bg-brand-600 py-3 text-sm font-bold text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {loading ? "Обработка..." : `Оплатить картой ${total.toLocaleString("ru-RU")} ₽`}
          </button>

          <p className="mt-4 text-center text-xs text-gray-400">
            Нажимая кнопку, вы соглашаетесь с{" "}
            <Link href="/offer" className="text-brand-600 underline">
              условиями оферты
            </Link>
          </p>
        </div>
      </div>
    </section>
  );
}

function AuthGate({
  info,
  planId,
  refParam,
}: {
  info: { label: string; durationDays: number; basePrice: number };
  planId: string;
  refParam: string | null;
}) {
  // Preserve ?ref= across the /login redirect so a referral link to /payment/...
  // still attributes after email verification.
  const loginUrl = `/login?next=/payment/${planId}${refParam ? `&ref=${encodeURIComponent(refParam)}` : ""}`;
  const botUrl = `https://t.me/${siteConfig.botUsername}?start=${planId}`;
  return (
    <section className="py-12">
      <div className="mx-auto max-w-lg px-4">
        <Link href="/#tariffs" className="text-sm font-medium text-brand-600 transition hover:text-brand-700 dark:text-brand-400">
          &larr; Назад к тарифам
        </Link>
        <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-8 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Оформление подписки</h1>
          <div className="mt-6 space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Тариф</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{info.label}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Период</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{info.durationDays} дней</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Подписка</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{info.basePrice.toLocaleString("ru-RU")} ₽</span>
            </div>
          </div>

          <div className="mt-8 rounded-xl bg-brand-50 p-4 text-center dark:bg-brand-900/20">
            <p className="text-sm font-medium text-gray-700 dark:text-gray-200">
              Войдите, чтобы оплатить подписку
            </p>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Или оплатите через Telegram-бота — без регистрации, в один клик
            </p>
          </div>

          <Link
            href={loginUrl}
            onClick={() => reachGoal("auth_gate_email", { plan: planId })}
            className="mt-4 block w-full rounded-xl bg-brand-600 py-3 text-center text-sm font-bold text-white transition hover:bg-brand-700"
          >
            Войти по email
          </Link>
          <a
            href={botUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => reachGoal("auth_gate_bot", { plan: planId })}
            className="mt-3 block w-full rounded-xl border border-gray-200 bg-white py-3 text-center text-sm font-bold text-gray-900 transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-100 dark:hover:bg-zinc-700"
          >
            Оплатить через Telegram-бот
          </a>
        </div>
      </div>
    </section>
  );
}
